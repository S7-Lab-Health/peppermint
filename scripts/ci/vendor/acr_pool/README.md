# Shared ACR policy export

`pool.py` and `private_endpoint.py` are exact exports of the revisions recorded
in their respective upstream manifests. Do not edit them here. Regenerate from
the infra repository with:

```sh
python3 scripts/acr/sync_pool_policy.py REV /path/to/peppermint --include-private-endpoint
```

Peppermint is public and cannot load an action from the private infra
repository. The deployment verifies this export's hash and uses the same
private journal, lease, build admission and cooldown policy as other callers.
After joining Tailnet, it verifies TLS and binds only the journal's Blob hostname
to its reviewed private address on the ephemeral runner. A read-only journal
metadata check must pass before the build can acquire its lease.
