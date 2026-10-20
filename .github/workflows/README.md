# Workflows

Every workflow pins actions by commit SHA. Workflows that touch GCP log in with Workload Identity Federation (no keys), and only from `main`.

**Repository secrets** (set once, e.g. `gh secret set GCP_PROJECT_ID`): `GCP_PROJECT_ID` and `GCP_WIF_PROVIDER` (`projects/<project number>/locations/global/workloadIdentityPools/github/providers/github-actions`; `infra/bootstrap/bootstrap.sh` prints it). They are not credentials; secrets keep them out of the logs.

| File | Trigger | Built in |
|---|---|---|
| `ci.yml` | pull_request to main: Terraform fmt/validate, tflint, shellcheck, ruff, unit tests, actionlint, Trivy config scan. Job `gateway`: the gateway in containers (LiteLLM, Postgres, Presidio, the OTel Collector, mocks for vLLM and the hosted model), every P2 test, and P3's span contract (`projects/03-llm-observability/tests/test_spans.py`: the trace fields P3's metrics are built on). No cloud login | P0 → P3 |
| `mirror.yml` | workflow_dispatch (`config`, default the BF16 model config), as the `mirror` service account: copies the pinned vLLM image (amd64) from Docker Hub into Artifact Registry with `crane copy`, and the model weights from Hugging Face into the models bucket one file at a time. Re-runs skip files already copied | P1 |
| `claude-review.yml` | pull_request | later |
| `foundation.yml` | workflow_dispatch from main (plan + apply `infra/foundation/`) | P0 |
| `cost-guard.yml` | schedule (06:00 UTC) + workflow_dispatch (`max_age_hours`, default 8): destroys older session clusters, opens an issue | P0 |
| `gpu-session.yml` | workflow_dispatch (`mode`: shakedown, full, bf16-bench, gateway-shakedown, gateway-full or obs-shakedown; `zone`; `spot`): `make up LAYERS=01` → `make test P=01` → measurements → diagnostics artifact (project ID, number and emails removed); then a separate `teardown` job (its own time limit, runs however the session job ends) → `make down` → `make status`. Shakedown: one short benchmark and one node-removal timing. Full: BF16 and FP8 benchmarks, the scale-out test, 3 optimized and 3 baseline cold starts. bf16-bench: only the BF16 benchmarks, to redo an invalid run. gateway-shakedown (P2): no GPU; the gateway with its test-only mock provider, then every P2 check (`make test P=02`) and diagnostics with pod resource use. gateway-full (P2): P1 serving FP8 plus the gateway; every P2 check, the demo transcript (`run.sh demo`), the gateway-overhead benchmark (straight to vLLM vs through the gateway, 10 and 50 concurrent, 3 rounds), then fallback after vLLM goes away and 20 more calls with vLLM gone. obs-shakedown (P3): P2 and P3 with no vLLM; every P3 check (`make test P=03`), the fallback alert end to end (skipped until P3-06's alert receiver exists), and a short spot GPU check (a pause pod asks for one GPU; Prometheus must get GKE's DCGM metrics), after which the GPU pool is set to 0 nodes. Results go to the results bucket as each step finishes | P0 → P3 |
| `release.yml` | tag `v*`: packages every chart in `projects/*/helm/` with `scripts/package_charts.sh` (each chart's version must equal the tag; `ci.yml` runs the same script on every PR), pushes them to GHCR (GitHub Container Registry) as OCI charts, and creates a GitHub release with generated notes and the packages attached. No cloud login. Not tagged yet | P2 |
| `evals.yml` | pull_request on models/prompts/gateway config/policies paths | P4 |
| `release-model.yml` | workflow_dispatch (candidate config) | P4 |

`gpu-session.yml` and `cost-guard.yml` must always end with the cluster destroyed (`if: always()` on the down step).
