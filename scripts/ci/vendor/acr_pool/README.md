# Shared ACR policy export

`pool.py` is an exact export of the revision recorded in `upstream.json`.
Do not edit it here. Regenerate it from the infra repository with:

```sh
python3 scripts/acr/sync_pool_policy.py REV /path/to/peppermint
```

Peppermint is public and cannot load an action from the private infra
repository. The deployment verifies this export's hash and uses the same
private journal, lease, build admission and cooldown policy as other callers.
