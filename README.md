# Halo Phone User Auth

This project deploys an Azure Function App that receives HaloPSA webhooks, validates Halo users against Microsoft Graph using certificate-based authentication, and sends or checks SMS verification codes through RingCentral. Infrastructure is delivered with Bicep and backed by Azure Key Vault for secret storage. A RingCentral call webhook is also provided so the RingCentral Embeddable widget can surface inbound/outbound call events inside Halo PSA while SMS validation remains available via the webhook.

## Solution overview
- **HaloPSA webhook endpoint**: HTTP-triggered Azure Function at `/api/halo/webhook` that accepts JSON payloads with `action` (`start` or `verify`), `userPrincipalName`, `phoneNumber`, and optional `code`. SMS is dispatched through RingCentral for both sending and validation.
- **Microsoft Graph validation**: Acquires an app-only access token with a client certificate (stored as a Key Vault secret) to confirm the user exists and retrieve their identifiers before any SMS call.
- **RingCentral SMS verification**: Generates codes in the function, sends them through RingCentral SMS, and validates against a code table in Azure Storage to survive cold starts and scale-out.
- **RingCentral call webhook**: `/api/ringcentral/incoming-call` accepts POST payloads from the RingCentral Embeddable widget and logs/acknowledges call events so Halo PSA can receive call context.
- **Secrets in Key Vault**: The Function App runs with a system-assigned managed identity that can read Key Vault secrets for Graph and SMS credentials.

## Repository structure
- `function_app/` – Azure Functions Python app and dependencies.
  - `HaloWebhook/` – HTTP trigger implementation for HaloPSA webhooks.
  - `requirements.txt` – Runtime dependencies.
- `infra/main.bicep` – Deploys the Function App, Consumption plan, Storage Account, Application Insights, Key Vault, and Graph/RingCentral secrets.
- `scripts/deploy.sh` – Example Azure CLI script to deploy the infrastructure and publish the function code.

## Configuration
Environment variables expected by the function runtime:

| Setting | Description |
| --- | --- |
| `KEY_VAULT_NAME` | Name of the Key Vault containing secrets. Set automatically by the Bicep template. |
| `GRAPH_TENANT_ID` | Azure AD tenant ID for Microsoft Graph. |
| `GRAPH_CLIENT_ID` | Application (client) ID used for the certificate credential. |
| `RINGCENTRAL_SERVER_URL` | Optional RingCentral platform URL override (defaults to production). |
| `RINGCENTRAL_CODE_TTL_MINUTES` | Optional expiration in minutes for RingCentral codes (defaults to `10`). |
| `RINGCENTRAL_CODE_TABLE` | Optional Azure Table name for RingCentral verification codes (defaults to `RingCentralCodes`). |

Key Vault secrets populated by the Bicep template:

| Secret name | Purpose |
| --- | --- |
| `graph-cert-pfx` | Base64-encoded PFX file containing the Graph app certificate. |
| `graph-cert-password` | Password for the PFX (empty string if none). |
| `ringcentral-client-id` | RingCentral application client ID. |
| `ringcentral-client-secret` | RingCentral application client secret. |
| `ringcentral-jwt` | RingCentral JWT used for authenticating the platform session. |
| `ringcentral-from-number` | Sender phone number for RingCentral SMS messages. |

> The function retrieves these secrets directly with `DefaultAzureCredential`, so no secret values are injected into app settings.

## Deployment
1. Export required environment variables for Azure CLI deployment:

```bash
export GRAPH_TENANT_ID=<tenant-id>
export GRAPH_CLIENT_ID=<client-id>
export GRAPH_CERT_PFX=$(base64 -w 0 path/to/certificate.pfx)
export GRAPH_CERT_PASSWORD=<pfx-password>
export RINGCENTRAL_CLIENT_ID=<client-id>
export RINGCENTRAL_CLIENT_SECRET=<client-secret>
export RINGCENTRAL_JWT=<jwt>
export RINGCENTRAL_FROM_NUMBER=<sender-number>
# Optional RingCentral deployment settings
export RINGCENTRAL_SERVER_URL=https://platform.ringcentral.com
export RINGCENTRAL_CODE_TTL_MINUTES=10
```

2. Run the deployment script (customize `BASE_NAME`, `RESOURCE_GROUP`, and `LOCATION` if needed):

```bash
./scripts/deploy.sh
```

The script deploys the infrastructure with Bicep and, if the Azure Functions Core Tools (`func`) are installed, publishes the function code to the newly created Function App.

## Webhook contract
- `POST /api/halo/webhook`
- Body examples:

```json
{ "action": "start", "userPrincipalName": "user@contoso.com", "phoneNumber": "+15551234567" }
{ "action": "verify", "userPrincipalName": "user@contoso.com", "phoneNumber": "+15551234567", "code": "123456" }
```

Responses:
- `202 Accepted` with `{ "status": "verification_started", "user": "<id>" }` when SMS dispatch succeeds.
- `200 OK` with `{ "status": "approved" | "denied", "user": "<id>" }` for verification attempts.
- `400` for malformed requests; `502` for downstream errors.

## Local development
1. Install the Azure Functions Core Tools and Python 3.11.
2. Create and activate a virtual environment, then install dependencies:

```bash
cd function_app
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Update `function_app/local.settings.json` with your tenant, client ID, and development storage connection. Use Azure CLI `az login` so the managed identity credentials can access Key Vault during local testing.
4. Start the function host:

```bash
func start
```

Send test payloads with `curl` or your preferred HTTP client to `http://localhost:7071/api/halo/webhook`.
