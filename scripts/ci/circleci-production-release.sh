#!/usr/bin/env bash
# CircleCI port of .github/workflows/deploy.yml; deliberately opt-in.
set -euo pipefail
set +x

die() { printf 'Release refused: %s\n' "$*" >&2; exit 1; }
require() { test -n "${!1:-}" || die "$1 is required"; }

test "${CIRCLE_BRANCH:-}" = main || die 'only main may release to production'
test -z "${CIRCLE_PR_NUMBER:-}" || die 'pull requests cannot release'
test "${CIRCLECI_PEPPERMINT_PRODUCTION_ENABLED:-}" = true || die 'release context is disabled'
require EXPECTED_RELEASE_SHA
[[ "$EXPECTED_RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] || die 'expected SHA must be a full commit SHA'
test "${CIRCLE_SHA1:-}" = "$EXPECTED_RELEASE_SHA" || die 'pipeline SHA differs from approved SHA'
test "$(git rev-parse HEAD)" = "$EXPECTED_RELEASE_SHA" || die 'checkout SHA differs from approved SHA'

for name in AZURE_CLIENT_ID AZURE_TENANT_ID AZURE_SUBSCRIPTION_ID \
  ACR_LOGIN_SERVER CONTAINER_APP_NAME RESOURCE_GROUP \
  ACR_COORDINATION_ACCOUNT ACR_COORDINATION_CONTAINER \
  TS_OAUTH_CLIENT_ID TS_OAUTH_SECRET \
  CIRCLE_OIDC_TOKEN_V3 CIRCLE_WORKFLOW_ID CIRCLE_JOB; do
  require "$name"
done

# These are the values currently used by the GitHub production workflow. Refuse
# an accidental deployment to a different registry or Container App.
test "$ACR_LOGIN_SERVER" = acraltairprod.azurecr.io || die 'unexpected ACR registry'
test "$CONTAINER_APP_NAME" = ca-peppermint-production || die 'unexpected ACA name'
test "$RESOURCE_GROUP" = rg-altair-prod || die 'unexpected resource group'
test "$ACR_COORDINATION_ACCOUNT" = altairasprod || die 'unexpected pool journal account'
test "$ACR_COORDINATION_CONTAINER" = acr-pool-coordination || die 'unexpected pool journal container'

# GitHub's production workflow joins the same tag before using the private
# journal. On a CircleCI VM the native CLI must do the same. The VM image must
# have a verified Tailscale installation before this release is enabled.
command -v tailscale >/dev/null || die 'Tailscale CLI is required on the VM'
command -v az >/dev/null || die 'Azure CLI is required on the VM'
umask 077
ts_secret_file=$(mktemp)
trap 'tailscale logout >/dev/null 2>&1 || true; rm -f "$ts_secret_file"' EXIT
printf '%s' "$TS_OAUTH_SECRET" > "$ts_secret_file"
tailscale up --client-id="$TS_OAUTH_CLIENT_ID" --client-secret="file:$ts_secret_file" \
  --advertise-tags=tag:ci-runner
tailscale status >/dev/null || die 'Tailnet is not connected'
unset TS_OAUTH_SECRET

az login --service-principal --username "$AZURE_CLIENT_ID" \
  --tenant "$AZURE_TENANT_ID" --federated-token "$CIRCLE_OIDC_TOKEN_V3" \
  --output none
az account set --subscription "$AZURE_SUBSCRIPTION_ID"
test "$(az account show --query id -o tsv)" = "$AZURE_SUBSCRIPTION_ID" || die 'Azure subscription mismatch'

# Use the exact hash-verified export now used by the GitHub production workflow.
helper=scripts/ci/vendor/acr_pool/pool.py
python3 - "$helper" scripts/ci/vendor/acr_pool/upstream.json <<'PY'
import hashlib
import json
import sys
from pathlib import Path
helper, manifest = map(Path, sys.argv[1:])
expected = json.loads(manifest.read_text())['sha256']
if hashlib.sha256(helper.read_bytes()).hexdigest() != expected:
    raise SystemExit('Shared ACR guard differs from its pinned export')
PY

export ACR_REGISTRY="${ACR_LOGIN_SERVER%.azurecr.io}"
export ACR_AGENT_POOL=agentpool-production
export ACR_COORDINATION_ACCOUNT ACR_COORDINATION_CONTAINER
export ACR_REQUEST_KEY="S7-Lab-Health/peppermint/${CIRCLE_WORKFLOW_ID}/${CIRCLE_JOB}/production-build"
python3 "$helper" build --key "$ACR_REQUEST_KEY" \
  --registry "$ACR_REGISTRY" --pool "$ACR_AGENT_POOL" \
  --account "$ACR_COORDINATION_ACCOUNT" --container "$ACR_COORDINATION_CONTAINER" \
  --wait 1800 -- --image "peppermint:$EXPECTED_RELEASE_SHA" --file dockerfile .

# Matches the GitHub workflow's target and SHA tag. The image is promoted only
# after the guarded ACR run reaches Succeeded.
image="$ACR_LOGIN_SERVER/peppermint:$EXPECTED_RELEASE_SHA"
az containerapp update --name "$CONTAINER_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" --image "$image" --output none
actual=$(az containerapp show --name "$CONTAINER_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" --query 'properties.template.containers[0].image' -o tsv)
test "$actual" = "$image" || die 'Container App image readback differs'
printf 'Promoted %s to %s\n' "$image" "$CONTAINER_APP_NAME"
