#!/usr/bin/env bash
# GitHub's existing main publisher, now staged to share the app-release lease.
# This source must not merge before the dedicated lock blob and both identities
# are provisioned and a two-provider contention probe has passed.
set -euo pipefail
set +x

refuse() { echo "Production release refused: $1" >&2; exit 1; }
[[ "${GITHUB_REF:-}" == refs/heads/main ]] || refuse 'only main can deploy'
[[ "${GITHUB_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || refuse 'invalid source SHA'
[[ "$(git rev-parse HEAD)" == "$GITHUB_SHA" ]] || refuse 'checkout differs from GitHub source'
[[ "${GITHUB_REPOSITORY:-}" == S7-Lab-Health/peppermint ]] || refuse 'unexpected GitHub repository'
[[ "${EXPECTED_GITHUB_IMAGE:-}" =~ ^acraltairprod\.azurecr\.io/peppermint@sha256:[0-9a-f]{64}$ ]] ||
  refuse 'missing exact image from owned GitHub ACR build'
python3 scripts/ci/check_release_lock_export.py >/dev/null

export RELEASE_LOCK_ACCOUNT=altairasprod
export RELEASE_LOCK_CONTAINER=peppermint-production-release
export RELEASE_LOCK_BLOB=peppermint-production.lock
export RELEASE_LOCK_PRIVATE_IP=10.0.4.5
export RELEASE_LOCK_ROUTE_DEVICE=tailscale0

check_main_head() {
  local listing head ref
  listing="$(GIT_TERMINAL_PROMPT=0 timeout 20 git ls-remote --exit-code origin refs/heads/main)" ||
    refuse 'cannot verify current GitHub main'
  read -r head ref <<< "$listing"
  [[ "$ref" == refs/heads/main && "$head" == "$GITHUB_SHA" ]] ||
    refuse 'main advanced beyond this release'
}

if [[ "${1:-}" == deploy-locked ]]; then
  [[ $# -eq 1 ]] || exit 2
  # This branch is reached only as a child of release_lock.sh run.
  bash scripts/ci/vendor/release_lock/release_lock.sh assert
  check_main_head
  python3 scripts/ci/circleci_peppermint_release.py deploy "$EXPECTED_GITHUB_IMAGE"
elif [[ "${1:-}" == run ]]; then
  [[ $# -eq 1 ]] || exit 2
  check_main_head
  bash scripts/ci/vendor/release_lock/release_lock.sh run -- \
    bash scripts/ci/github-production-release.sh deploy-locked
else
  refuse 'expected run or deploy-locked'
fi
