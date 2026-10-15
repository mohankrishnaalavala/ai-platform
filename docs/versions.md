# Pinned versions

Record every pinned version here when you add it (Terraform providers, GKE version, Helm charts, images, Python packages, models).

| Component | Version | Pinned in | Date |
|---|---|---|---|
| Terraform | 1.5.7 (last open-source MPL-2.0 release) | `infra/foundation/versions.tf` | 2026-10-15 |
| Google provider (`hashicorp/google`) | 8.7.0 | `infra/foundation/versions.tf`, `.terraform.lock.hcl` | 2026-10-15 |
| tflint (Docker image, local lint) | `ghcr.io/terraform-linters/tflint:v0.64.0` | run by hand until `ci.yml` (P0-08) | 2026-10-15 |
| shellcheck (Docker image, local lint) | `koalaman/shellcheck:v0.11.0` | run by hand until `ci.yml` (P0-08) | 2026-10-14 |
| GKE (session cluster) | `1.36.4-gke.1391002`, REGULAR channel | `infra/session/main.tf` | 2026-10-15 |
