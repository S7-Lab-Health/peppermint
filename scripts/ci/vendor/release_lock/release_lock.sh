#!/usr/bin/env bash
# Shared source-only Blob lease helper for GitHub Actions and CircleCI.
# No caller is wired to it yet. Use one separately provisioned container/blob
# per app and environment, with container-scoped RBAC. Both providers must use
# the same exact scope and wrap the full release in `run`; every image/config
# mutation, including rollback, must call `assert` immediately beforehand.
# Failed/interrupted runs retain the infinite lease for manual reconciliation.
set -euo pipefail

account="${RELEASE_LOCK_ACCOUNT:-}"
container="${RELEASE_LOCK_CONTAINER:-}"
blob="${RELEASE_LOCK_BLOB:-}"
private_ip="${RELEASE_LOCK_PRIVATE_IP:-}"
route_device="${RELEASE_LOCK_ROUTE_DEVICE:-tailscale0}"

refuse() { echo "Release lock refused: $1" >&2; exit 1; }

[[ "$account" =~ ^[a-z0-9]{3,24}$ ]] || refuse 'invalid storage account'
[[ "$container" =~ ^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$ ]] || refuse 'invalid lock container'
[[ "$container" != acr-pool-coordination ]] || refuse 'ACR journal container cannot hold app deployment locks'
[[ "$blob" =~ ^[a-z0-9][a-z0-9-]{1,80}\.lock$ ]] || refuse 'invalid lock blob'
[[ "$route_device" =~ ^[a-zA-Z0-9_-]+$ ]] || refuse 'invalid route device'
python3 - "$private_ip" <<'PY' || refuse 'expected address must be RFC1918 IPv4'
import ipaddress
import sys
try:
    address = ipaddress.IPv4Address(sys.argv[1])
    ranges = [ipaddress.IPv4Network(block) for block in
              ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
    assert any(address in block for block in ranges)
except (ValueError, AssertionError):
    raise SystemExit(1)
PY

scope="$account/$container/$blob"
host="$account.blob.core.windows.net"
blob_args=(--account-name "$account" --container-name "$container"
           --blob-name "$blob" --auth-mode login --only-show-errors --output none)

check_route() {
  local addresses route
  addresses="$(getent ahostsv4 "$host" | awk '{print $1}' | sort -u)" || refuse 'private DNS lookup failed'
  [[ "$addresses" == "$private_ip" ]] || refuse 'Blob DNS does not match the reviewed private endpoint'
  route="$(ip -4 route get "$private_ip")" || refuse 'private route lookup failed'
  [[ " $route " == *" dev $route_device "* ]] || refuse 'Blob endpoint does not route through the reviewed tailnet device'
}

assert_owner() {
  [[ "${ALTAIR_RELEASE_SCOPE:-}" == "$scope" ]] || refuse 'lease scope differs from the reviewed release'
  [[ "${ALTAIR_RELEASE_LEASE_ID:-}" =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ ]] || refuse 'lease ID is absent or malformed'
  check_route
  az storage blob lease renew "${blob_args[@]}" \
    --lease-id "$ALTAIR_RELEASE_LEASE_ID" >/dev/null 2>&1 || refuse 'lease lost; do not mutate the target'
}

case "${1:-}" in
  route)
    [[ $# -eq 1 ]] || exit 2
    check_route
    ;;
  assert)
    [[ $# -eq 1 ]] || exit 2
    assert_owner
    ;;
  run)
    [[ $# -ge 3 && "$2" == -- ]] || {
      echo 'Usage: release_lock.sh run -- COMMAND [ARGS...]' >&2
      exit 2
    }
    [[ -z "${ALTAIR_RELEASE_LEASE_ID:-}" && -z "${ALTAIR_RELEASE_SCOPE:-}" ]] || refuse 'nested lease is not allowed'
    check_route
    lease="$(python3 -c 'import uuid; print(uuid.uuid4())')"
    az storage blob lease acquire "${blob_args[@]}" \
      --lease-duration -1 --proposed-lease-id "$lease" >/dev/null 2>&1 || \
      refuse 'lock blob is absent, held, or inaccessible'
    export ALTAIR_RELEASE_LEASE_ID="$lease" ALTAIR_RELEASE_SCOPE="$scope"
    child_done=0
    # shellcheck disable=SC2329 # Invoked by the EXIT trap below.
    release_on_exit() {
      local status=$?
      trap - EXIT
      if [[ "$child_done" -eq 1 && "$status" -eq 0 ]]; then
        # A successful child is not enough: prove ownership immediately before
        # release, or retain the lock for manual reconciliation.
        assert_owner
        az storage blob lease release "${blob_args[@]}" \
          --lease-id "$lease" >/dev/null 2>&1 || refuse 'release failed; manual recovery required'
      else
        echo 'Release failed or interrupted; lease retained for manual reconciliation' >&2
      fi
      exit "$status"
    }
    trap release_on_exit EXIT
    shift 2
    set +e
    "$@"
    status=$?
    set -e
    child_done=1
    exit "$status"
    ;;
  *)
    echo 'Usage: release_lock.sh {route|assert|run -- COMMAND [ARGS...]}' >&2
    exit 2
    ;;
esac
