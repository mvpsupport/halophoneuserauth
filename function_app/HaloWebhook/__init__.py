import base64
import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import azure.functions as func
import msal
import requests
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
from ringcentral import SDK as RingCentralSDK
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
SECRET_CACHE: Dict[str, str] = {}
RINGCENTRAL_CODES: Dict[str, dict[str, Any]] = {}


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

    provider = _sms_provider()

    if action == "start":
        try:
            _send_verification(phone_number, provider)
            return func.HttpResponse(
                json.dumps({"status": "verification_started", "user": graph_user.get("id")}),
                status_code=202,
                mimetype="application/json",
            )
        except (TwilioRestException, Exception) as tex:  # pragma: no cover - logging only
            logging.exception("SMS send failed: %s", tex)
            return func.HttpResponse("Unable to send verification", status_code=502)
    elif action == "verify":
        if not (phone_number and verification_code):
            return func.HttpResponse(
                "phoneNumber and code are required for verification",
                status_code=400,
            )
        try:
            approved = _check_verification(phone_number, verification_code, provider)
            status = "approved" if approved else "denied"
            return func.HttpResponse(
                json.dumps({"status": status, "user": graph_user.get("id")}),
                status_code=200,
                mimetype="application/json",
            )
        except (TwilioRestException, Exception) as tex:  # pragma: no cover - logging only
            logging.exception("Verification failed: %s", tex)
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


def _twilio_client() -> TwilioClient:
    account_sid = _get_secret("twilio-account-sid")
    auth_token = _get_secret("twilio-auth-token")
    return TwilioClient(account_sid, auth_token)


def _sms_provider() -> str:
    provider = os.environ.get("SMS_PROVIDER", "twilio").lower()
    if provider not in {"twilio", "ringcentral"}:
        raise RuntimeError("SMS_PROVIDER must be either 'twilio' or 'ringcentral'")
    return provider


def _send_verification(phone_number: str, provider: str) -> None:
    if provider == "twilio":
        _send_twilio_verification(phone_number)
        return
    _send_ringcentral_verification(phone_number)


def _send_twilio_verification(phone_number: str) -> None:
    service_sid = _get_secret("twilio-verify-service-sid")
    client = _twilio_client()
    client.verify.v2.services(service_sid).verifications.create(to=phone_number, channel="sms")


def _check_verification(phone_number: str, code: str, provider: str) -> bool:
    if provider == "twilio":
        return _check_twilio_verification(phone_number, code)
    return _check_ringcentral_verification(phone_number, code)


def _check_twilio_verification(phone_number: str, code: str) -> bool:
    service_sid = _get_secret("twilio-verify-service-sid")
    client = _twilio_client()
    verification_check = client.verify.v2.services(service_sid).verification_checks.create(to=phone_number, code=code)
    return verification_check.status == "approved"


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

    RINGCENTRAL_CODES[phone_number] = {"code": code, "expires_at": expires_at}

    platform.post(
        "/restapi/v1.0/account/~/extension/~/sms",
        {
            "from": {"phoneNumber": from_number},
            "to": [{"phoneNumber": phone_number}],
            "text": f"Your verification code is {code}",
        },
    )


def _check_ringcentral_verification(phone_number: str, code: str) -> bool:
    entry = RINGCENTRAL_CODES.get(phone_number)
    if not entry:
        return False

    now = datetime.now(timezone.utc)
    if now > entry.get("expires_at"):
        del RINGCENTRAL_CODES[phone_number]
        return False

    if entry.get("code") == code:
        del RINGCENTRAL_CODES[phone_number]
        return True

    return False


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
