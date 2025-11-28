#!/usr/bin/env bash
set -euo pipefail

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is required" >&2
  exit 1
fi

BASE_NAME=${BASE_NAME:-halophone}
RESOURCE_GROUP=${RESOURCE_GROUP:-halo-auth-rg}
LOCATION=${LOCATION:-eastus}
DEPLOYMENT_NAME=${DEPLOYMENT_NAME:-halo-deploy-$(date +%s)}

if [[ -z "${GRAPH_TENANT_ID:-}" || -z "${GRAPH_CLIENT_ID:-}" || -z "${GRAPH_CERT_PFX:-}" || -z "${GRAPH_CERT_PASSWORD:-}" || -z "${TWILIO_ACCOUNT_SID:-}" || -z "${TWILIO_AUTH_TOKEN:-}" || -z "${TWILIO_VERIFY_SID:-}" ]]; then
  echo "Required environment variables are missing." >&2
  echo "Set GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CERT_PFX (base64), GRAPH_CERT_PASSWORD, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_VERIFY_SID." >&2
  exit 1
fi

az group create --name "$RESOURCE_GROUP" --location "$LOCATION"

az deployment group create \
  --name "$DEPLOYMENT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --template-file infra/main.bicep \
  --parameters baseName="$BASE_NAME" \
               graphTenantId="$GRAPH_TENANT_ID" \
               graphClientId="$GRAPH_CLIENT_ID" \
               graphCertPfx="$GRAPH_CERT_PFX" \
               graphCertPassword="$GRAPH_CERT_PASSWORD" \
               twilioAccountSid="$TWILIO_ACCOUNT_SID" \
               twilioAuthToken="$TWILIO_AUTH_TOKEN" \
               twilioVerifyServiceSid="$TWILIO_VERIFY_SID"

FUNCTION_APP_NAME=$(az deployment group show --resource-group "$RESOURCE_GROUP" --name "$DEPLOYMENT_NAME" --query "properties.outputs.functionAppName.value" -o tsv)

if command -v func >/dev/null 2>&1; then
  pushd function_app >/dev/null
  func azure functionapp publish "$FUNCTION_APP_NAME" --python
  popd >/dev/null
else
  echo "Azure Functions Core Tools (func) not found; skipping function publish step." >&2
fi
