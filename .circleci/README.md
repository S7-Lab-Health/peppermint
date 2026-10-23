# Peppermint production release pilot

The source of truth for current GitHub deployment behavior is
`.github/workflows/deploy.yml`. The opt-in CircleCI job reproduces its
`dockerfile` ACR build of `peppermint:circleci-production-<full SHA>-<build number>` and ACA image update on
`main`. The Circle job calls the **same checked-in, hash-verified ACR pool
guard export** as the GitHub workflow in `scripts/ci/vendor/acr_pool/`.
The export's source revision is recorded in `upstream.json`. GitHub Actions
remains enabled.

Both providers now stage a second, separate lock around ACA promotion and
rollback: the exact hash-verified export of `altair-infra`'s
`scripts/ci/release_lock.sh` in `scripts/ci/vendor/release_lock/`.
The Circle build tag is unique to its run, and both providers resolve an
immutable digest from their own successful ACR run. GitHub carries that digest
between its build and deploy jobs; neither provider promotes a mutable tag.
The lock scope is the dedicated `altairasprod` container
`peppermint-production-release`, blob `peppermint-production.lock`, reached
through the reviewed private endpoint `10.0.4.5`. It is **not** the ACR
pool-journal blob. The helper retains its infinite lease on failure for
manual reconciliation.

No trigger is configured. The pipeline parameter
`run-peppermint-production` defaults to `false`. For a bounded manual
rehearsal, the operator must select `main`, pass that parameter as `true`,
pass the full checked-out commit as `expected-release-sha`, and approve the
workflow hold. The job also requires
`CIRCLECI_PEPPERMINT_PRODUCTION_ENABLED=true` in its restricted context.
Before both the ACR build and the Container App update, the job reads the
current GitHub `main` ref and refuses to publish an older approved SHA.
CircleCI's repository, organization, branch, job and V3 OIDC checkout/config
claims must all match this release. Azure verifies the signed OIDC token.

The `altair-peppermint-production-release` context must be restricted to the
Peppermint project and `main` branch, with no SSH reruns. It needs:

| Variable | Purpose |
| --- | --- |
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | Dedicated CircleCI OIDC federated identity, trusted only for the Peppermint pipeline on `main` |
| `ACR_LOGIN_SERVER=acraltairprod.azurecr.io` | Same production registry as GitHub |
| `CONTAINER_APP_NAME=ca-peppermint-production`, `RESOURCE_GROUP=rg-altair-prod` | Same production ACA target as GitHub |
| `ACR_COORDINATION_ACCOUNT`, `ACR_COORDINATION_CONTAINER` | Existing shared pool journal, not a new lease store |
| `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET` | OAuth client scoped to ephemeral `tag:ci-runner` Tailscale enrollment, matching the GitHub workflow's private journal route |
| `CIRCLECI_PEPPERMINT_BLOB_IP` | Reviewed private IPv4 endpoint for `altairasprod.blob.core.windows.net`; no public Blob fallback |
| `CIRCLECI_PEPPERMINT_PRODUCTION_ENABLED=true` | Explicit enable switch, absent during staging |

Azure permissions need to cover federated login, ACR build submission and
monitoring, ACR agent-pool scaling, read/write access to the journal blob,
and updating/reading **only** `ca-peppermint-production`. No credentials are
in the repository. CircleCI does not inherit the GitHub environment secrets
or GitHub's Azure OIDC trust.

The Medium VM installs Azure CLI from Microsoft's signed Ubuntu repository if
it is absent, and uses a SHA-256-pinned official Tailscale client archive. Its
native TUN joins the tailnet only after source and OIDC checks pass; the job
checks that existing CircleCI control routes and DNS stay intact, verifies the
private Blob route and TLS endpoint, and logs out on exit or failure. This
reproduces GitHub's `tailscale/github-action` private-journal access without
copying its credentials. The route guard comes from the Tether native-network
draft; the CircleCI VM path still needs a live probe.
The VM setup follows [CircleCI's Linux VM guide](https://circleci.com/docs/guides/execute/using-linuxvm/),
[Microsoft's Azure CLI apt instructions](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-linux?pivots=apt),
and [Tailscale's Linux static-binary instructions](https://tailscale.com/docs/install/linux).

The job reads the unique ACR run ID returned by that shared guard and resolves
the immutable digest from that exact successful run before deploying. It
captures the previous ACA image and resolves its immutable ACR digest before
any update, verifies that the new latest revision is the
ready, active, healthy revision running that digest, and checks the public
ingress. A failed rollout restores the captured image only if its own candidate
image is still current; the result is retained as a CircleCI artifact. The
dedicated Azure identity needs ACR manifest metadata read access to pin the
previous image, and the job refuses deployment if that read fails. This
does not reverse any database migration or seed performed when the container
starts.

**Release gate:** The OAuth client must be separately provisioned to CircleCI;
it is not inherited from GitHub. Azure OIDC subject, journal access, exact ACA
roles, and this VM's native Tailnet route need a scoped live probe.
Before merging this branch into `main`, create the dedicated lock container
and blob and grant container-scoped Blob lease access to **both** GitHub and
CircleCI production identities. Verify both providers reach the same private
endpoint and run a controlled cross-provider contention test. Until then the
GitHub workflow change is deliberately held in this draft PR; merging it would
make GitHub deployments fail closed on the absent lock.
Do not enable automatic merge triggers until those gates and a controlled
production release pass. The existing GitHub workflow has no post-deploy
readiness gate or automatic rollback. These new safety checks have only offline
tests; the live rollout and rollback have not been exercised.

This pilot covers only `.github/workflows/deploy.yml`. The separate
`nightly.yml` and manually dispatched `release.yml` publish multi-architecture
images to Docker Hub and remain out of scope, as does branch-only `dev.yml`.
