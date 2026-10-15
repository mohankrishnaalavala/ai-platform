# Workflows (planned)

Every workflow pins actions by commit SHA and logs in to GCP with Workload Identity Federation (no keys).

| File | Trigger | Built in |
|---|---|---|
| `ci.yml` | pull_request | P0 |
| `claude-review.yml` | pull_request | later |
| `foundation.yml` | workflow_dispatch from main (plan + apply `infra/foundation/`) | P0 |
| `cost-guard.yml` | schedule (nightly) + workflow_dispatch | P0 |
| `gpu-session.yml` | workflow_dispatch (inputs: project, action = test/bench) | P0 → P1 |
| `deploy.yml` | push to main (only if a session cluster exists) | P1 |
| `release.yml` | tag `v*` | P2 |
| `evals.yml` | pull_request on models/prompts/gateway config/policies paths | P4 |
| `release-model.yml` | workflow_dispatch (candidate config) | P4 |

`gpu-session.yml` and `cost-guard.yml` must always end with the cluster destroyed (`if: always()` on the down step).
