# infra/

Built in [P0](../projects/00-foundation/).

| Folder | Lifetime | What's in it |
|---|---|---|
| `bootstrap/` | One-time | Script: enable APIs, create the versioned Terraform state bucket |
| `foundation/` | Persistent ($[X]/month idle, not measured yet) | Workload Identity Federation for GitHub, deployer service account, Artifact Registry, GCS buckets (models, results, audit), Secret Manager placeholders, budget + alerts |
| `session/` | Per session (`make up` / `make down`) | GKE Standard zonal cluster, system pool, spot L4 GPU pool (min 0, max 2), add-ons |
| `modules/` | — | `github-wif`, `budget`, `buckets`, `gke-cluster`, `gpu-node-pool` |

Rules:
- Every resource is labeled `app=ai-platform`, `layer=0X`, `owner`, and `session` for session resources.
- `profile = "dev"` (default) or `"enterprise"` (private nodes + NAT, Cloud SQL, managed monitoring).
- Pin provider versions; record them in `docs/versions.md`.
- Never commit `*.tfstate` or `terraform.tfvars` with real IDs. Use `terraform.tfvars.example`.
