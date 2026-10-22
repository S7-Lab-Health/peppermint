#!/usr/bin/env bash
# Prepare the Azure CLI that GitHub's ubuntu-latest runner provides by default.
set -euo pipefail
set +x

# shellcheck source=/dev/null
. /etc/os-release
test "$ID" = ubuntu && test "$VERSION_ID" = 24.04 && test "$(uname -m)" = x86_64 || {
  echo 'Expected a CircleCI Ubuntu 24.04 amd64 VM' >&2
  exit 2
}

if ! command -v az >/dev/null 2>&1; then
  # Microsoft's signed apt repository, following its Ubuntu installation guide.
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates curl gnupg
  keyring="$(mktemp)"
  trap 'rm -f "$keyring"' EXIT
  curl -fsSL --retry 3 --max-time 60 https://packages.microsoft.com/keys/microsoft.asc |
    gpg --dearmor > "$keyring"
  sudo install -d -m 755 /etc/apt/keyrings
  sudo install -m 644 "$keyring" /etc/apt/keyrings/microsoft.gpg
  printf 'Types: deb\nURIs: https://packages.microsoft.com/repos/azure-cli/\nSuites: noble\nComponents: main\nArchitectures: amd64\nSigned-by: /etc/apt/keyrings/microsoft.gpg\n' |
    sudo tee /etc/apt/sources.list.d/azure-cli.sources >/dev/null
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq azure-cli
fi

az version --query '"azure-cli"' -o tsv >/dev/null
if ! az extension show --name containerapp --output none >/dev/null 2>&1; then
  az extension add --name containerapp --only-show-errors --output none
fi
az containerapp update --help >/dev/null
