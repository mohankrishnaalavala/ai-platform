# Pinned versions

Record every pinned version here when you add it (Terraform providers, GKE version, Helm charts, images, Python packages, models).

| Component | Version | Pinned in | Date |
|---|---|---|---|
| Terraform | 1.5.7 (last open-source MPL-2.0 release) | `infra/foundation/versions.tf` | 2026-10-15 |
| Google provider (`hashicorp/google`) | 8.7.0 | `infra/foundation/versions.tf`, `.terraform.lock.hcl` | 2026-10-15 |
| tflint (Docker image) | `ghcr.io/terraform-linters/tflint:v0.64.0@sha256:1c595f42…` | `ci.yml`; same command on the laptop | 2026-10-15 |
| shellcheck (Docker image) | `koalaman/shellcheck:v0.11.0@sha256:61862eba…` | `ci.yml`; same command on the laptop | 2026-10-15 |
| GKE (session cluster) | `1.36.4-gke.1495002`, REGULAR channel (the newest version all three us-central1 L4 zones offer; `1.36.4-gke.1391002` was already gone from -c) | `infra/session/main.tf` | 2026-10-15 |
| actionlint (Docker image) | `rhysd/actionlint:1.7.12@sha256:b1934ee5…` | `ci.yml` | 2026-10-15 |
| Trivy (Docker image; not `trivy-action`, whose tags were hijacked in March 2026) | `aquasec/trivy:0.75.0@sha256:af6acf9a…` | `ci.yml` | 2026-10-15 |
| `actions/checkout` | v7.0.1 (`3d3c42e5…`) | all workflows | 2026-10-15 |
| `hashicorp/setup-terraform` | v4.0.1 (`dfe3c3f8…`) | all workflows | 2026-10-15 |
| `google-github-actions/auth` | v3.0.0 (`7c6bc770…`) | `gpu-session.yml`, `cost-guard.yml`, `mirror.yml` | 2026-10-15 |
| `google-github-actions/setup-gcloud` | v3.0.1 (`aa5489c8…`), gcloud CLI 587.0.0 | `gpu-session.yml`, `cost-guard.yml`, `mirror.yml` | 2026-10-15 |
| ruff (Docker image) | `ghcr.io/astral-sh/ruff:0.16.9@sha256:fd4a00e8…` | `ci.yml` | 2026-10-16 |
| vLLM image | `vllm/vllm-openai:v0.30.0`, linux/amd64 manifest `sha256:5f5e5352…` (released 2026-09-22; v0.31.0 was 11 days old) | `config/models/*.yaml`; copied to Artifact Registry by `mirror.yml` | 2026-10-16 |
| Model | `Qwen/Qwen2.5-7B-Instruct`, revision `a09a3545…` (Apache 2.0, not gated) | `config/models/*.yaml` | 2026-10-16 |
| crane (go-containerregistry) | v0.22.1, release tarball checked by sha256 `0ab7a1d6…` | `mirror.yml` | 2026-10-16 |
