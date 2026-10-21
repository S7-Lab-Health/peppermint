# Shared ACR policy export

`pool.py` is an exact export of the revision recorded in `upstream.json`.
Do not edit it here. Regenerate it from the infra repository with:

```sh
python3 scripts/acr/sync_pool_policy.py REV /path/to/peppermint --include-private-endpoint
```

Peppermint is public and cannot load an action from the private infra
repository. The deployment verifies this export's hash and uses the same
private journal, lease, build admission and cooldown policy as other callers.

`private_endpoint.py` is also an exact export, recorded in its own source manifest.
Both exports are hash-verified before execution. The endpoint helper verifies TLS
and binds only the storage hostname on the ephemeral runner.
