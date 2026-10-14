# infra/

Built in [P0](../projects/00-foundation/).

| Folder | Lifetime | What's in it |
|---|---|---|
| `bootstrap/` | One-time | Script, run once: enable APIs, create the versioned Terraform state bucket, the terraform service account and Workload Identity Federation for GitHub |
| `foundation/` | Persistent ($[X]/month idle, not measured yet) | VPC (Virtual Private Cloud) network with Private Google Access, deployer and node service accounts, Artifact Registry, Cloud Storage (GCS) buckets (models, results, audit), budget + alerts |
| `session/` | Per session (`make up` / `make down`) | GKE (Google Kubernetes Engine) Standard zonal cluster with private nodes (no public IPs), system pool, spot NVIDIA L4 GPU (graphics processing unit) pool (min 0, max 2), add-ons |
| `modules/` | — | `network`, `github-wif`, `budget`, `buckets`, `gke-cluster`, `gpu-node-pool` |

Rules:
- Every resource that supports labels is labeled `app=ai-platform`, `layer=0X`, `owner`, and `session` for session resources. (Networks, subnets and firewall rules don't take labels.)
- `profile = "dev"` (default) or `"enterprise"` (adds Cloud SQL and managed monitoring).
- No Cloud NAT (network address translation) yet, so nodes can't reach the internet. The first project that needs it adds it to `session/`, so `make down` removes it.
- Terraform never uses keys: GitHub Actions logs in through Workload Identity Federation, and the laptop impersonates the same service accounts.
- Pin provider versions; record them in `docs/versions.md`.
- Never commit `*.tfstate` or `terraform.tfvars` with real IDs. Use `terraform.tfvars.example`.
