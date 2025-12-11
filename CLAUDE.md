# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Azure Function App that receives HaloPSA webhooks, validates users against Microsoft Graph using certificate-based authentication, and sends SMS verification codes through RingCentral. Infrastructure deployed with Bicep, secrets managed via Azure Key Vault.

## Architecture

### Authentication Flow
1. **HaloPSA webhook** → HTTP trigger at `/api/halo/webhook`
2. **Microsoft Graph validation** → Certificate-based app-only authentication to validate user existence
3. **SMS provider** → RingCentral for code generation/validation
4. **Secrets management** → Function App uses system-assigned managed identity to read Key Vault secrets

### Key Components

**function_app/HaloWebhook/__init__.py**: Main webhook handler
- `_acquire_graph_token()`: Uses MSAL with certificate credentials from Key Vault
- `_load_certificate()`: Loads PFX from Key Vault, extracts private key and thumbprint
- `_send_ringcentral_verification()`: Generates codes and sends via RingCentral SMS
- `_check_ringcentral_verification()`: Validates codes from in-memory storage

**function_app/lib/halo_api.py**: HaloPSA API client (not currently used by webhook)
- OAuth2 authentication with Halo PSA
- Methods to retrieve agents and companies by ID or phone

**function_app/lib/validation_code.py**: In-memory code management utilities (for RingCentral)
- Generates numeric/alphanumeric codes
- Stores codes with TTL expiration
- Validates and auto-cleans expired entries

**infra/main.bicep**: Infrastructure as Code
- Deploys Function App (Consumption Y1 plan), Storage, App Insights, Key Vault
- Stores Graph certificate (PFX + password) and RingCentral credentials as Key Vault secrets
- Grants Function App managed identity `get` and `list` permissions on Key Vault secrets

### SMS Provider Architecture

Function generates 6-digit numeric codes, stores in-memory (`RINGCENTRAL_CODES` dict), sends via RingCentral SMS API, and validates from memory. Code expiration is configurable via `RINGCENTRAL_CODE_TTL_MINUTES`.

## Development Commands

### Local Development
```bash
# Setup
cd function_app
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run locally (requires Azure Functions Core Tools)
func start
```

**Local testing requirements**:
- Azure Functions Core Tools installed
- Python 3.11
- `function_app/local.settings.json` configured with `KEY_VAULT_NAME`, `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`
- Azure CLI `az login` for Key Vault access via DefaultAzureCredential

### Deployment

```bash
# Set required environment variables
export GRAPH_TENANT_ID=<tenant-id>
export GRAPH_CLIENT_ID=<client-id>
export GRAPH_CERT_PFX=$(base64 -w 0 path/to/certificate.pfx)
export GRAPH_CERT_PASSWORD=<pfx-password>
export RINGCENTRAL_CLIENT_ID=<client-id>
export RINGCENTRAL_CLIENT_SECRET=<secret>
export RINGCENTRAL_JWT=<jwt-token>
export RINGCENTRAL_FROM_NUMBER=<phone-number>

# Deploy infrastructure and function code
./scripts/deploy.sh
```

**Deployment script** ([scripts/deploy.sh](scripts/deploy.sh)):
- Creates resource group
- Deploys Bicep template with secret parameters
- Publishes function code if `func` CLI is available

## Configuration

### Environment Variables
| Variable | Description |
|----------|-------------|
| `KEY_VAULT_NAME` | Key Vault name (set by Bicep template) |
| `GRAPH_TENANT_ID` | Azure AD tenant ID |
| `GRAPH_CLIENT_ID` | App client ID for Graph authentication |
| `RINGCENTRAL_SERVER_URL` | Optional RingCentral platform URL (defaults to production) |
| `RINGCENTRAL_CODE_TTL_MINUTES` | RingCentral code expiration in minutes (default: 10) |

### Key Vault Secrets
All secrets retrieved via `DefaultAzureCredential`:
- `graph-cert-pfx`: Base64-encoded PFX certificate
- `graph-cert-password`: PFX password
- `ringcentral-client-id`, `ringcentral-client-secret`, `ringcentral-jwt`, `ringcentral-from-number`

## Webhook API Contract

**POST /api/halo/webhook**

Start verification:
```json
{
  "action": "start",
  "userPrincipalName": "user@contoso.com",
  "phoneNumber": "+15551234567"
}
```
Response: `202 Accepted` with `{ "status": "verification_started", "user": "<graph-user-id>" }`

Verify code:
```json
{
  "action": "verify",
  "userPrincipalName": "user@contoso.com",
  "phoneNumber": "+15551234567",
  "code": "123456"
}
```
Response: `200 OK` with `{ "status": "approved" | "denied", "user": "<graph-user-id>" }`

## Important Implementation Details

### Certificate Handling
The function loads the Graph client certificate from Key Vault as a base64-encoded PFX, decodes it, and extracts:
- Private key (PEM format, PKCS8)
- SHA1 thumbprint
- Public certificate

MSAL `ConfidentialClientApplication` uses this as `client_credential` for certificate-based authentication.

### Secret Caching
`SECRET_CACHE` dict caches Key Vault secrets in memory to avoid repeated Key Vault calls. Cache persists across function executions within the same instance.

### RingCentral Code Storage
`RINGCENTRAL_CODES` dict stores verification codes in-memory. This is **not suitable for multi-instance deployments** - consider Redis for production scale.

### Webhook Payload Validation
`_parse_payload()` and `_require_string()` perform strict validation:
- JSON must be an object
- All required fields must be non-empty strings
- `action` must be exactly "start" or "verify"
- Returns `400` for malformed payloads

## Testing

Send test requests to local endpoint:
```bash
# Start verification
curl -X POST http://localhost:7071/api/halo/webhook \
  -H "Content-Type: application/json" \
  -d '{"action":"start","userPrincipalName":"user@contoso.com","phoneNumber":"+15551234567"}'

# Verify code
curl -X POST http://localhost:7071/api/halo/webhook \
  -H "Content-Type: application/json" \
  -d '{"action":"verify","userPrincipalName":"user@contoso.com","phoneNumber":"+15551234567","code":"123456"}'
```
