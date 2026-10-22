#!/usr/bin/env bash
# CircleCI wrapper for the existing .github/workflows/deploy.yml release.
set -euo pipefail
set +x

die() { printf 'Release refused: %s\n' "$*" >&2; exit 1; }
require() { test -n "${!1:-}" || die "$1 is required"; }

check_main_head() {
  # A manual approval can sit pending while main advances. Check before both
  # the ACR build and the production Container App update.
  local remote listing head ref
  remote="$(git remote get-url origin)"
  case "$remote" in
    https://github.com/S7-Lab-Health/peppermint|https://github.com/S7-Lab-Health/peppermint.git|\
git@github.com:S7-Lab-Health/peppermint|git@github.com:S7-Lab-Health/peppermint.git) ;;
    *) die 'unexpected GitHub origin' ;;
  esac
  listing="$(GIT_TERMINAL_PROMPT=0 timeout 20 git ls-remote --exit-code origin refs/heads/main)" ||
    die 'cannot verify current GitHub main'
  read -r head ref <<< "$listing"
  test "$ref" = refs/heads/main && test "$head" = "$EXPECTED_RELEASE_SHA" ||
    die 'GitHub main has advanced beyond the reviewed release SHA'
}

check_oidc_claims() {
  # Local source checks are defense in depth. Azure independently verifies the
  # signed token during federated login.
  python3 - <<'PY'
import base64
import json
import os

def refuse(message):
    raise SystemExit(f'Release refused: {message}')

try:
    payload = os.environ['CIRCLE_OIDC_TOKEN_V3'].split('.')[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
except (IndexError, ValueError, KeyError):
    refuse('invalid CircleCI OIDC token')

expected = {
    'oidc.circleci.com/org-id': 'c5d6ae7c-9659-4728-bd36-2105d7f3448c',
    'oidc.circleci.com/checkout-source': 'github.com/S7-Lab-Health/peppermint',
    'oidc.circleci.com/checkout-ref': 'refs/heads/main',
    'oidc.circleci.com/config-source': 'github.com/S7-Lab-Health/peppermint',
    'oidc.circleci.com/config-ref': 'refs/heads/main',
    'oidc.circleci.com/project-id': '0706e010-61eb-496b-a0e1-d7a0e96a7b6a',
}
for key, value in expected.items():
    if claims.get(key) != value:
        refuse('CircleCI OIDC source or project differs')
if claims.get('oidc.circleci.com/ssh-rerun') is not False:
    refuse('SSH reruns cannot release')
PY
}

check_shared_policy() {
  python3 - <<'PY'
import hashlib
import json
from pathlib import Path
import re

folder = Path('scripts/ci/vendor/acr_pool')
manifest = json.loads((folder / 'upstream.json').read_text())
if not (
    manifest.get('repository') == 'S7-Lab-Health/altair-infra'
    and manifest.get('path') == '.github/actions/acr-agentpool-scale-up/pool.py'
    and re.fullmatch(r'[0-9a-f]{40}', manifest.get('revision', ''))
    and hashlib.sha256((folder / 'pool.py').read_bytes()).hexdigest() == manifest.get('sha256')
):
    raise SystemExit('Release refused: shared ACR policy differs from pinned export')
PY
}

validate() {
  test "${CIRCLE_BRANCH:-}" = main || die 'only main may release to production'
  test -z "${CIRCLE_PULL_REQUEST:-}" && test -z "${CIRCLE_PR_NUMBER:-}" &&
    test -z "${CIRCLE_TAG:-}" || die 'PRs and tags cannot release'
  test "${CIRCLE_PROJECT_USERNAME:-}" = S7-Lab-Health || die 'unexpected CircleCI project owner'
  test "${CIRCLE_PROJECT_REPONAME:-}" = peppermint || die 'unexpected CircleCI repository'
  test "${CIRCLE_PROJECT_ID:-}" = 0706e010-61eb-496b-a0e1-d7a0e96a7b6a ||
    die 'unexpected CircleCI project ID'
  test "${CIRCLE_ORGANIZATION_ID:-}" = c5d6ae7c-9659-4728-bd36-2105d7f3448c ||
    die 'unexpected CircleCI organization'
  test "${CIRCLE_JOB:-}" = release-production || die 'unexpected CircleCI job'
  test "${CIRCLECI_PEPPERMINT_PRODUCTION_ENABLED:-}" = true || die 'release context is disabled'
  require EXPECTED_RELEASE_SHA
  [[ "$EXPECTED_RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] || die 'expected SHA must be a full commit SHA'
  test "${CIRCLE_SHA1:-}" = "$EXPECTED_RELEASE_SHA" || die 'pipeline SHA differs from approved SHA'
  test "$(git rev-parse HEAD)" = "$EXPECTED_RELEASE_SHA" || die 'checkout SHA differs from approved SHA'

  for name in AZURE_CLIENT_ID AZURE_TENANT_ID AZURE_SUBSCRIPTION_ID \
    ACR_LOGIN_SERVER CONTAINER_APP_NAME RESOURCE_GROUP \
    ACR_COORDINATION_ACCOUNT ACR_COORDINATION_CONTAINER \
    TS_OAUTH_CLIENT_ID TS_OAUTH_SECRET CIRCLECI_PEPPERMINT_BLOB_IP \
    CIRCLE_OIDC_TOKEN_V3 CIRCLE_PROJECT_ID CIRCLE_WORKFLOW_ID CIRCLE_BUILD_NUM; do
    require "$name"
  done
  [[ "$CIRCLE_BUILD_NUM" =~ ^[0-9]+$ ]] || die 'invalid CircleCI build number'
  [[ "$CIRCLE_WORKFLOW_ID" =~ ^[0-9a-f-]{36}$ ]] || die 'invalid CircleCI workflow ID'
  test "$AZURE_SUBSCRIPTION_ID" = 02a40345-607b-434e-bacc-c385ac136c1b ||
    die 'unexpected Azure subscription'
  test "$ACR_LOGIN_SERVER" = acraltairprod.azurecr.io || die 'unexpected ACR registry'
  test "$CONTAINER_APP_NAME" = ca-peppermint-production || die 'unexpected ACA name'
  test "$RESOURCE_GROUP" = rg-altair-prod || die 'unexpected resource group'
  test "$ACR_COORDINATION_ACCOUNT" = altairasprod || die 'unexpected pool journal account'
  test "$ACR_COORDINATION_CONTAINER" = acr-pool-coordination ||
    die 'unexpected pool journal container'
  check_private_blob_ip
  check_oidc_claims
  check_shared_policy
  check_main_head
}

check_private_blob_ip() {
  python3 - "$CIRCLECI_PEPPERMINT_BLOB_IP" <<'PY'
import ipaddress
import sys
address = ipaddress.ip_address(sys.argv[1])
if address.version != 4 or not any(address in ipaddress.ip_network(cidr) for cidr in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
    raise SystemExit('Release refused: journal endpoint is not a private IPv4 address')
PY
}

check_journal_route() {
  check_private_blob_ip
  local host resolved route status
  host=altairasprod.blob.core.windows.net
  resolved="$(getent ahostsv4 "$host" | awk 'NR == 1 {print $1}')"
  test "$resolved" = "$CIRCLECI_PEPPERMINT_BLOB_IP" ||
    die 'pool journal DNS differs from reviewed private endpoint'
  route="$(ip -4 route get "$resolved")"
  [[ "$route" =~ (^|[[:space:]])dev[[:space:]]tailscale0([[:space:]]|$) ]] ||
    die 'pool journal is not routed through Tailscale'
  status="$(curl --silent --show-error --noproxy '*' --proto '=https' \
    --tlsv1.2 --max-redirs 0 --connect-timeout 5 --max-time 10 \
    --resolve "${host}:443:${CIRCLECI_PEPPERMINT_BLOB_IP}" --output /dev/null \
    --write-out '%{remote_ip} %{ssl_verify_result} %{http_code}' \
    "https://${host}/?comp=list")" || die 'pool journal private TLS probe failed'
  [[ "$status" =~ ^${CIRCLECI_PEPPERMINT_BLOB_IP//./\\.}\ 0\ [234][0-9][0-9]$ ]] ||
    die 'pool journal private TLS probe returned an unexpected endpoint'
}

run_release() {
  validate
  check_journal_route
  command -v az >/dev/null || die 'Azure CLI is unavailable on CircleCI VM'
  az login --service-principal --username "$AZURE_CLIENT_ID" \
    --tenant "$AZURE_TENANT_ID" --federated-token "$CIRCLE_OIDC_TOKEN_V3" \
    --output none
  az account set --subscription "$AZURE_SUBSCRIPTION_ID"
  test "$(az account show --query id -o tsv)" = "$AZURE_SUBSCRIPTION_ID" ||
    die 'Azure subscription mismatch after login'

  # Exact same repository-pinned ACR helper and image-build inputs as GitHub.
  check_main_head
  export ACR_REGISTRY="${ACR_LOGIN_SERVER%.azurecr.io}"
  export ACR_AGENT_POOL=agentpool-production
  export ACR_COORDINATION_ACCOUNT ACR_COORDINATION_CONTAINER
  # The vendored GitHub helper records caller metadata. Supply truthful
  # CircleCI equivalents instead of leaving the GitHub fields empty.
  export GITHUB_REPOSITORY=S7-Lab-Health/peppermint
  export GITHUB_WORKFLOW=circleci-peppermint-production-release
  export GITHUB_RUN_ID="$CIRCLE_WORKFLOW_ID"
  export GITHUB_RUN_ATTEMPT="$CIRCLE_BUILD_NUM"
  export GITHUB_JOB="$CIRCLE_JOB"
  export GITHUB_SHA="$EXPECTED_RELEASE_SHA"
  export ACR_REQUEST_KEY="circleci/S7-Lab-Health/peppermint/${CIRCLE_WORKFLOW_ID}/${CIRCLE_BUILD_NUM}/${CIRCLE_JOB}/image"
  local image run_id run_log digest
  run_log="$(mktemp)"
  trap 'rm -f "$run_log"' RETURN
  python3 scripts/ci/vendor/acr_pool/pool.py build \
    --registry "$ACR_REGISTRY" --pool "$ACR_AGENT_POOL" \
    --account "$ACR_COORDINATION_ACCOUNT" --container "$ACR_COORDINATION_CONTAINER" \
    --key "$ACR_REQUEST_KEY" --wait 1800 -- \
    --image "peppermint:$EXPECTED_RELEASE_SHA" --file dockerfile . | tee "$run_log"

  check_main_head
  run_id="$(python3 scripts/ci/circleci_peppermint_release.py run-id "$run_log")"
  digest="$(python3 scripts/ci/circleci_peppermint_release.py digest "$run_id" "$EXPECTED_RELEASE_SHA")"
  image="$ACR_LOGIN_SERVER/peppermint@$digest"
  check_main_head
  python3 scripts/ci/circleci_peppermint_release.py deploy "$image"
  printf 'Promoted %s to %s\n' "$image" "$CONTAINER_APP_NAME"
}

case "${1:-}" in
  validate) validate ;;
  network) check_journal_route ;;
  run) run_release ;;
  *) die 'expected validate, network or run' ;;
esac
