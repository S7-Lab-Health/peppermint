# Peppermint production release pilot

The source of truth for current GitHub deployment behavior is
`.github/workflows/deploy.yml`. The opt-in CircleCI job reproduces its
`dockerfile` ACR build of `peppermint:<full SHA>` and ACA image update on
`main`. The Circle job calls the **same checked-in, hash-verified ACR pool
guard export** as the GitHub workflow in `scripts/ci/vendor/acr_pool/`.
The export's source revision is recorded in `upstream.json`. GitHub Actions
remains enabled.

No trigger is configured. The pipeline parameter
`run-peppermint-production` defaults to `false`. For a bounded manual
rehearsal, the operator must select `main`, pass that parameter as `true`,
pass the full checked-out commit as `expected-release-sha`, and approve the
workflow hold. The job also requires
`CIRCLECI_PEPPERMINT_PRODUCTION_ENABLED=true` in its restricted context.

The `altair-peppermint-production-release` context must be restricted to the
Peppermint project and `main` branch, with no SSH reruns. It needs:

| Variable | Purpose |
| --- | --- |
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | Dedicated CircleCI OIDC federated identity, trusted only for the Peppermint pipeline on `main` |
| `ACR_LOGIN_SERVER=acraltairprod.azurecr.io` | Same production registry as GitHub |
| `CONTAINER_APP_NAME=ca-peppermint-production`, `RESOURCE_GROUP=rg-altair-prod` | Same production ACA target as GitHub |
| `ACR_COORDINATION_ACCOUNT`, `ACR_COORDINATION_CONTAINER` | Existing shared pool journal, not a new lease store |
| `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET` | OAuth client scoped to ephemeral `tag:ci-runner` Tailscale enrollment, matching the GitHub workflow's private journal route |
| `CIRCLECI_PEPPERMINT_PRODUCTION_ENABLED=true` | Explicit enable switch, absent during staging |

Azure permissions need to cover federated login, ACR build submission and
monitoring, ACR agent-pool scaling, read/write access to the journal blob,
and updating/reading **only** `ca-peppermint-production`. No credentials are
in the repository. CircleCI does not inherit the GitHub environment secrets
or GitHub's Azure OIDC trust.

**Release gate:** Both source workflows now use the same ACR journal policy,
but the CircleCI VM still needs a pinned, tested Tailscale installation and
daemon before it can reach that private journal. The OAuth client must be
separately provisioned to CircleCI; it is not inherited from GitHub. Azure
OIDC subject, journal access, and exact ACA roles need a scoped live probe.
Do not enable automatic merge triggers until those gates and a controlled
production release pass. The existing GitHub workflow has no post-deploy
readiness gate or automatic rollback; this draft adds an image readback but
has not exercised rollout or rollback. Record the previous image digest and
verify the live revision before cutover.
