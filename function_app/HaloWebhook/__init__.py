import base64
import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import azure.functions as func
import msal
import requests
from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
from ringcentral import SDK as RingCentralSDK

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
SECRET_CACHE: Dict[str, str] = {}


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Halo webhook received")

    try:
        action, user_principal_name, phone_number, verification_code = _parse_payload(req)
    except ValueError as exc:
        return func.HttpResponse(str(exc), status_code=400)

    try:
        token = _acquire_graph_token()
        graph_user = _get_graph_user(token, user_principal_name)
    except Exception as ex:  # pragma: no cover - defensive logging
        logging.exception("Graph user validation failed: %s", ex)
        return func.HttpResponse("Unable to validate user", status_code=502)

    if action == "start":
        try:
            _send_ringcentral_verification(phone_number)
            return func.HttpResponse(
                json.dumps({"status": "verification_started", "user": graph_user.get("id"), "provider": "ringcentral"}),
                status_code=202,
                mimetype="application/json",
            )
        except Exception as ex:  # pragma: no cover - logging only
            logging.exception("SMS send failed: %s", ex)
            return func.HttpResponse("Unable to send verification", status_code=502)
    elif action == "verify":
        if not (phone_number and verification_code):
            return func.HttpResponse(
                "phoneNumber and code are required for verification",
                status_code=400,
            )
        try:
            approved = _check_ringcentral_verification(phone_number, verification_code)
            status = "approved" if approved else "denied"
            return func.HttpResponse(
                json.dumps({"status": status, "user": graph_user.get("id"), "provider": "ringcentral"}),
                status_code=200,
                mimetype="application/json",
            )
        except Exception as ex:  # pragma: no cover - logging only
            logging.exception("Verification failed: %s", ex)
            return func.HttpResponse("Unable to validate code", status_code=502)
    else:
        return func.HttpResponse("Unsupported action", status_code=400)


def _secret_client() -> SecretClient:
    vault_name = os.environ.get("KEY_VAULT_NAME")
    if not vault_name:
        raise RuntimeError("KEY_VAULT_NAME is not configured")
    vault_uri = f"https://{vault_name}.vault.azure.net"
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return SecretClient(vault_url=vault_uri, credential=credential)


def _get_secret(name: str) -> str:
    if name in SECRET_CACHE:
        return SECRET_CACHE[name]
    client = _secret_client()
    secret_value = client.get_secret(name).value
    SECRET_CACHE[name] = secret_value
    return secret_value


def _load_certificate() -> Dict[str, str]:
    pfx_b64 = _get_secret("graph-cert-pfx")
    password = _get_secret("graph-cert-password") or None

    pfx_bytes = base64.b64decode(pfx_b64)
    private_key, cert, _ = load_key_and_certificates(pfx_bytes, password.encode() if password else None)
    if not private_key or not cert:
        raise RuntimeError("PFX did not contain a private key and certificate")

    private_key_pem = private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    ).decode()
    thumbprint = cert.fingerprint(hashes.SHA1()).hex()
    public_certificate = cert.public_bytes(Encoding.PEM).decode()

    return {
        "private_key": private_key_pem,
        "thumbprint": thumbprint,
        "public_certificate": public_certificate,
    }


def _acquire_graph_token() -> str:
    tenant_id = os.environ.get("GRAPH_TENANT_ID")
    client_id = os.environ.get("GRAPH_CLIENT_ID")
    if not (tenant_id and client_id):
        raise RuntimeError("GRAPH_TENANT_ID and GRAPH_CLIENT_ID must be set")

    certificate = _load_certificate()
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.ConfidentialClientApplication(
        client_id=client_id,
        authority=authority,
        client_credential=certificate,
    )
    result = app.acquire_token_silent(scopes=[GRAPH_SCOPE], account=None)
    if not result:
        result = app.acquire_token_for_client(scopes=[GRAPH_SCOPE])
    if "access_token" not in result:
        raise RuntimeError(f"Unable to acquire Graph token: {result}")
    return result["access_token"]


def _get_graph_user(token: str, user_principal_name: str) -> Dict[str, Any]:
    url = f"https://graph.microsoft.com/v1.0/users/{user_principal_name}"
    params = {"$select": "id,displayName,userPrincipalName,mobilePhone"}
    response = requests.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    if response.status_code != 200:
        raise RuntimeError(f"Graph responded with {response.status_code}: {response.text}")
    return response.json()


def _ringcentral_client() -> RingCentralSDK:
    client_id = _get_secret("ringcentral-client-id")
    client_secret = _get_secret("ringcentral-client-secret")
    jwt = _get_secret("ringcentral-jwt")
    server_url = os.environ.get("RINGCENTRAL_SERVER_URL", "https://platform.ringcentral.com")

    sdk = RingCentralSDK(client_id, client_secret, server_url)
    platform = sdk.platform()
    platform.login(jwt=jwt)
    return sdk


def _send_ringcentral_verification(phone_number: str) -> None:
    sdk = _ringcentral_client()
    platform = sdk.platform()

    from_number = _get_secret("ringcentral-from-number")
    code = f"{random.randint(0, 999999):06d}"
    ttl_minutes = int(os.environ.get("RINGCENTRAL_CODE_TTL_MINUTES", "10"))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)

    _store_ringcentral_code(phone_number, code, expires_at)

    platform.post(
        "/restapi/v1.0/account/~/extension/~/sms",
        {
            "from": {"phoneNumber": from_number},
            "to": [{"phoneNumber": phone_number}],
            "text": f"Your verification code is {code}",
        },
    )


def _check_ringcentral_verification(phone_number: str, code: str) -> bool:
    entry = _read_ringcentral_code(phone_number)
    if not entry:
        return False

    expires_at_raw = entry.get("ExpiresAt")
    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
    except Exception:
        expires_at = None

    now = datetime.now(timezone.utc)
    if not expires_at or now > expires_at:
        _delete_ringcentral_code(phone_number)
        return False

    if entry.get("Code") == code:
        _delete_ringcentral_code(phone_number)
        return True

    return False


def _ringcentral_code_table() -> TableServiceClient:
    connection_string = os.environ.get("AzureWebJobsStorage") or os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not connection_string:
        raise RuntimeError("AzureWebJobsStorage connection string is not configured")
    table_name = os.environ.get("RINGCENTRAL_CODE_TABLE", "RingCentralCodes")
    service = TableServiceClient.from_connection_string(conn_str=connection_string)
    service.create_table_if_not_exists(table_name=table_name)
    return service.get_table_client(table_name=table_name)


def _store_ringcentral_code(phone_number: str, code: str, expires_at: datetime) -> None:
    client = _ringcentral_code_table()
    entity = {
        "PartitionKey": "ringcentral",
        "RowKey": _ringcentral_row_key(phone_number),
        "PhoneNumber": phone_number,
        "Code": code,
        "ExpiresAt": expires_at.isoformat(),
    }
    client.upsert_entity(mode=UpdateMode.REPLACE, entity=entity)


def _read_ringcentral_code(phone_number: str) -> Optional[Dict[str, Any]]:
    client = _ringcentral_code_table()
    try:
        return dict(client.get_entity("ringcentral", _ringcentral_row_key(phone_number)))
    except ResourceNotFoundError:
        return None


def _delete_ringcentral_code(phone_number: str) -> None:
    client = _ringcentral_code_table()
    try:
        client.delete_entity("ringcentral", _ringcentral_row_key(phone_number))
    except ResourceNotFoundError:
        return


def _ringcentral_row_key(phone_number: str) -> str:
    sanitized = phone_number.strip()
    return sanitized.replace("/", "_").replace("\\", "_")


def _parse_payload(req: func.HttpRequest) -> tuple[str, str, str, str | None]:
    try:
        payload = req.get_json()
    except ValueError:
        raise ValueError("Invalid JSON payload")

    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")

    action = _require_string(payload.get("action"), "action").lower()
    if action not in {"start", "verify"}:
        raise ValueError("action must be either 'start' or 'verify'")

    user_principal_name = _require_string(payload.get("userPrincipalName"), "userPrincipalName")
    phone_number = _require_string(payload.get("phoneNumber"), "phoneNumber") if action in {"start", "verify"} else ""
    verification_code = None

    if action == "verify":
        verification_code = _require_string(payload.get("code"), "code")

    return action, user_principal_name, phone_number, verification_code


def _require_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} is required")
    return value
