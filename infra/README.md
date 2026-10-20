# infra/

Built in [P0](../projects/00-foundation/).

| Folder | Lifetime | What's in it |
|---|---|---|
| `bootstrap/` | One-time | Script, run once: enable APIs, create the versioned Terraform state bucket, the terraform service account, Workload Identity Federation for GitHub, and the custom role the deployer uses for the session's Cloud NAT |
| `foundation/` | Persistent ($[X]/month idle, not measured yet) | VPC (Virtual Private Cloud) network with Private Google Access, deployer, mirror and node service accounts, Workload Identity grants for the P1 pods, Artifact Registry, Cloud Storage (GCS) buckets (models, results, audit), budget + alerts |
| `session/` | Per session (`make up` / `make down`) | GKE (Google Kubernetes Engine) Standard zonal cluster with private nodes (no public IPs) and Dataplane V2 (enforces NetworkPolicies; from P3), system pool (one `e2-standard-8` from P3; with the GPU node that is the project's whole 16-vCPU quota, so no extra node can start mid-session, not even an upgrade's surge node), spot NVIDIA L4 GPU (graphics processing unit) pool (min 0, max 2), a `gpu-baseline` pool without image streaming (min 0, max 1, for the baseline cold start), Cloud NAT (network address translation), add-ons |
| `modules/` | — | `network`, `github-wif`, `budget`, `buckets`, `gke-cluster` and `gpu-node-pool` (P0-05) |

Rules:
- Every resource that supports labels is labeled `app=ai-platform`, `layer=0X`, `owner`, and `session` for session resources. (Networks, subnets and firewall rules don't take labels.)
- A `profile` setting (`dev` or `enterprise`: Cloud SQL, managed monitoring) comes when a project needs it.
- Cloud NAT lives in `session/` (added in P1), so `make down` removes it. It gives the private nodes outbound internet only; Google assigns its IP and nothing can connect in. Routers and NATs take no labels, so `make status` lists routers.
- Terraform never uses keys: GitHub Actions logs in through Workload Identity Federation, and the laptop impersonates the same service accounts.
- Pin provider versions; record them in `docs/versions.md`.
- Never commit `*.tfstate` or `terraform.tfvars` with real IDs. Use `terraform.tfvars.example`.
