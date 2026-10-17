# Workflows

Every workflow pins actions by commit SHA. Workflows that touch GCP log in with Workload Identity Federation (no keys), and only from `main`.

**Repository secrets** (set once, e.g. `gh secret set GCP_PROJECT_ID`): `GCP_PROJECT_ID` and `GCP_WIF_PROVIDER` (`projects/<project number>/locations/global/workloadIdentityPools/github/providers/github-actions`; `infra/bootstrap/bootstrap.sh` prints it). They are not credentials; secrets keep them out of the logs.

| File | Trigger | Built in |
|---|---|---|
| `ci.yml` | pull_request to main: Terraform fmt/validate, tflint, shellcheck, ruff, unit tests, actionlint, Trivy config scan. No cloud login | P0 |
| `mirror.yml` | workflow_dispatch (`config`, default the BF16 model config), as the `mirror` service account: copies the pinned vLLM image (amd64) from Docker Hub into Artifact Registry with `crane copy`, and the model weights from Hugging Face into the models bucket one file at a time. Re-runs skip files already copied | P1 |
| `claude-review.yml` | pull_request | later |
| `foundation.yml` | workflow_dispatch from main (plan + apply `infra/foundation/`) | P0 |
| `cost-guard.yml` | schedule (06:00 UTC) + workflow_dispatch (`max_age_hours`, default 8): destroys older session clusters, opens an issue | P0 |
| `gpu-session.yml` | workflow_dispatch (`mode`: shakedown, full or bf16-bench; `zone`; `spot`): `make up LAYERS=01` → `make test P=01` → measurements → diagnostics artifact (project ID, number and emails removed); then a separate `teardown` job (its own time limit, runs however the session job ends) → `make down` → `make status`. Shakedown: one short benchmark and one node-removal timing. Full: BF16 and FP8 benchmarks, the scale-out test, 3 optimized and 3 baseline cold starts. bf16-bench: only the BF16 benchmarks, to redo an invalid run. Results go to the results bucket as each step finishes | P0 → P1 |
| `release.yml` | tag `v*` | P2 |
| `evals.yml` | pull_request on models/prompts/gateway config/policies paths | P4 |
| `release-model.yml` | workflow_dispatch (candidate config) | P4 |

`gpu-session.yml` and `cost-guard.yml` must always end with the cluster destroyed (`if: always()` on the down step).
