#!/usr/bin/env bash
# Temporary native tailnet route for the Peppermint shared ACR journal endpoint.
set -euo pipefail
umask 077

[[ "${CIRCLE_BUILD_NUM:-}" =~ ^[0-9]+$ ]] || {
  echo 'A CircleCI build number is required' >&2
  exit 2
}
runtime="/tmp/altair-peppermint-production-${CIRCLE_BUILD_NUM}"
blob_host='altairasprod.blob.core.windows.net'

cleanup() {
  if [ -x "$runtime/tailscale" ] && [ -S "$runtime/tailscaled.sock" ]; then
    sudo "$runtime/tailscale" --socket="$runtime/tailscaled.sock" logout >/dev/null 2>&1 || true
  fi
  if [ -f "$runtime/hosts.before" ]; then
    sudo cp -- "$runtime/hosts.before" /etc/hosts
  fi
  if [ -f "$runtime/daemon.pid" ]; then
    pid="$(cat "$runtime/daemon.pid")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && [ -r "/proc/$pid/cmdline" ] &&
      tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq -- "$runtime/tailscaled.sock"; then
      sudo kill "$pid" >/dev/null 2>&1 || true
    fi
  fi
  if [ -f "$runtime/owns-interface" ]; then
    sudo ip link delete tailscale0 >/dev/null 2>&1 || true
  fi
  sudo rm -rf -- "$runtime"
}

case "${1:-}" in
  cleanup) cleanup; exit 0 ;;
  run) ;;
  *) echo 'Expected run or cleanup' >&2; exit 2 ;;
esac

# All source, authorization and OIDC checks precede enrollment.
bash scripts/ci/circleci-production-release.sh validate
: "${TS_OAUTH_CLIENT_ID:?Tailscale OAuth client ID required}"
: "${TS_OAUTH_SECRET:?Tailscale OAuth secret required}"
test -c /dev/net/tun
if ip link show dev tailscale0 >/dev/null 2>&1; then
  echo 'Refusing to reuse an existing tailscale0 interface' >&2
  exit 2
fi
[ ! -e "$runtime" ] || { echo 'Tailnet runtime already exists' >&2; exit 2; }
mkdir -m 700 "$runtime"
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
cp -- /etc/resolv.conf "$runtime/resolv.before"
cp -- /etc/hosts "$runtime/hosts.before"
ip -j -4 route show table all > "$runtime/routes.before.json"
ip -j -4 rule show > "$runtime/rules.before.json"
python3 -B scripts/ci/circleci_peppermint_route_guard.py sentinels \
  "$runtime/routes.before.json" "$runtime/resolv.before" > "$runtime/sentinels"
while IFS= read -r address; do
  printf '%s\t%s\n' "$address" "$(ip -4 route get "$address")"
done < "$runtime/sentinels" > "$runtime/route-get.before"
printf '%s' "$TS_OAUTH_SECRET" > "$runtime/authkey"
unset TS_OAUTH_SECRET

# Pinned official static client, matching the other Altair CircleCI drafts.
curl -fsSL --retry 3 --max-time 120 \
  https://pkgs.tailscale.com/stable/tailscale_1.104.1_amd64.tgz \
  -o "$runtime/tailscale.tgz"
printf '108d1d96ecf410d305571e173516f27038f870919a9b89c914a167c6d33a4528  %s\n' \
  "$runtime/tailscale.tgz" | sha256sum -c -
tar -xzf "$runtime/tailscale.tgz" -C "$runtime" --strip-components=1
touch "$runtime/owns-interface"
# shellcheck disable=SC2024
sudo "$runtime/tailscaled" --tun=tailscale0 \
  --state="$runtime/tailscaled.state" \
  --socket="$runtime/tailscaled.sock" > "$runtime/daemon.log" 2>&1 &
printf '%s' "$!" > "$runtime/daemon.pid"
for _attempt in $(seq 1 30); do
  [ -S "$runtime/tailscaled.sock" ] && break
  sleep 1
done
test -S "$runtime/tailscaled.sock"
sudo "$runtime/tailscale" --socket="$runtime/tailscaled.sock" up \
  --client-id="$TS_OAUTH_CLIENT_ID" \
  --client-secret="file:$runtime/authkey" \
  --advertise-tags=tag:ci-runner \
  --hostname="circleci-peppermint-production-${CIRCLE_BUILD_NUM}" \
  --accept-routes=true --accept-dns=false --timeout=60s
rm -f "$runtime/authkey"

ip -j -4 route show table all > "$runtime/routes.after.json"
ip -j -4 rule show > "$runtime/rules.after.json"
python3 -B scripts/ci/circleci_peppermint_route_guard.py verify \
  "$runtime/routes.before.json" "$runtime/routes.after.json" \
  "$runtime/rules.before.json" "$runtime/rules.after.json"
while IFS= read -r address; do
  printf '%s\t%s\n' "$address" "$(ip -4 route get "$address")"
done < "$runtime/sentinels" > "$runtime/route-get.after"
cmp -s "$runtime/route-get.before" "$runtime/route-get.after" || {
  echo 'Tailnet changed a CircleCI VM control route' >&2
  exit 2
}
cmp -s /etc/resolv.conf "$runtime/resolv.before" || {
  echo 'Tailnet changed runner DNS configuration' >&2
  exit 2
}
ip -4 route get "$CIRCLECI_PEPPERMINT_BLOB_IP" | grep -Eq 'dev tailscale0([[:space:]]|$)' || {
  echo 'Blob journal endpoint is not routed through Tailscale' >&2
  exit 2
}

# Only the journal host uses the reviewed private mapping. HTTPS retains the
# real hostname for certificate validation; /etc/hosts is restored on exit.
if awk -v host="$blob_host" '{for (i = 2; i <= NF; i++) if ($i == host) found = 1}
  END {exit !found}' /etc/hosts; then
  echo 'Blob journal host already has a local mapping' >&2
  exit 2
fi
printf '%s\t%s\n' "$CIRCLECI_PEPPERMINT_BLOB_IP" "$blob_host" | sudo tee -a /etc/hosts >/dev/null
resolved="$(getent ahostsv4 "$blob_host" | awk 'NR == 1 {print $1}')"
[ "$resolved" = "$CIRCLECI_PEPPERMINT_BLOB_IP" ] || {
  echo 'Blob journal hostname did not resolve to the reviewed private endpoint' >&2
  exit 2
}
bash scripts/ci/circleci-production-release.sh network

bash scripts/ci/circleci-production-release.sh run
