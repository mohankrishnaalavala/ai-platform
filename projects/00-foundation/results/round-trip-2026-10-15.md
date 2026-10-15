# Session round trip: `make up` → `make down` (2026-10-15)

Two `make up` → `make down` round trips in a row, the first sessions run on this platform. The goal: both work, and `make status` is empty after each.

## Method

- From the laptop: `make up`, then `make test P=00` and other checks, then `make down`. `make down` ends with `make status`.
- Each `make` command is timed with `date +%s` before and after (wall clock, whole seconds). The time includes `terraform init`, the one-hour token fetch and, for `up`, `get-credentials` and `kubectl get nodes`. The per-resource times are Terraform's own "complete after" lines.
- Run 1 in `us-central1-a`, run 2 in `us-central1-b` (`make up ZONE=us-central1-b`), to also exercise the zone variable.
- Between up and down, both runs tried to start one GPU node (`gcloud container clusters resize --node-pool=gpu --num-nodes=1`). It failed every time: the zone had no L4 capacity (`ZONE_RESOURCE_POOL_EXHAUSTED`), first for spot in `-a` and `-b`, then on demand in `-b` (`gpu_spot=false`, which replaced the empty GPU pool). No GPU VM was created. The GPU pool was at 0 nodes during every timing below.

## Results

| Run | Zone | `make up` | cluster | system pool | GPU pool (0 nodes) | `make down` | GPU pool | system pool | cluster | `make status` after |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | us-central1-a | 716 s (11m56s) | 10m37s | 1m2s | 22s | 618 s (10m18s) | 21s | 4m34s | 5m15s | nothing billable |
| 2 | us-central1-b | 699 s (11m39s) | 10m22s | 1m3s | 22s | 624 s (10m24s) | 21s | 4m53s | 5m4s | nothing billable |

After each `make down`, these lists were also empty (not filtered by label): clusters, instances, disks, forwarding rules, addresses, target pools, backend services, instance groups, instance templates, network endpoint groups, and firewall rules on the `ai-platform` network. Terraform's session state was empty, and a foundation `terraform plan -detailed-exitcode` exited 0 after run 1.

Not measured: cost per session (the billing console shows it later; see `costs.md` when it exists).

## Setup

- Laptop: Intel Core i7-9750H (macOS). The laptop only runs the commands; the time is spent in GCP.
- Terraform 1.5.7, Google provider 8.7.0, GKE `1.36.4-gke.1391002` (REGULAR channel), Google Cloud SDK 538.0.0, gke-gcloud-auth-plugin 1.30.0, kubectl client 1.32.2.
- Cluster: zonal GKE Standard, private nodes, DNS-based control plane endpoint only. System pool 1× `e2-standard-4`; GPU pool `g2-standard-8` + 1× L4, autoscaling 0–2.
- Config: commit `cd235d7` (session Terraform and Makefile). Run 2 also had a one-line Makefile change that only applies in GitHub Actions. SHA-256 of the Makefile, `infra/session/*.tf` and both modules' `*.tf` as used in run 2, hashed together (`shasum -a 256` of each file, then of that list; first 16 hex digits): `65831f67b6a36f46`.

## Raw output

Run 1, `make up` (started 2026-10-15T20:13:06Z, exit 0, 716 s):
```
data.terraform_remote_state.foundation: Read complete after 0s
module.cluster.google_container_cluster.this: Creation complete after 10m37s
module.gpu_pool.google_container_node_pool.this: Creation complete after 22s
module.cluster.google_container_node_pool.system: Creation complete after 1m2s
Apply complete! Resources: 3 added, 0 changed, 0 destroyed.
```

Run 1, `make down` (started 2026-10-15T20:31:04Z, exit 0, 618 s):
```
data.terraform_remote_state.foundation: Read complete after 0s
module.gpu_pool.google_container_node_pool.this: Destruction complete after 21s
module.cluster.google_container_node_pool.system: Destruction complete after 4m34s
module.cluster.google_container_cluster.this: Destruction complete after 5m15s
Destroy complete! Resources: 3 destroyed.
container clusters:        none
compute instances:         none
compute disks:             none
compute forwarding-rules:  none
compute addresses:         none
nothing billable is running
```

Run 2, `make up ZONE=us-central1-b` (started 2026-10-15T20:42:44Z, exit 0, 699 s):
```
data.terraform_remote_state.foundation: Read complete after 0s
module.cluster.google_container_cluster.this: Creation complete after 10m22s
module.gpu_pool.google_container_node_pool.this: Creation complete after 22s
module.cluster.google_container_node_pool.system: Creation complete after 1m3s
Apply complete! Resources: 3 added, 0 changed, 0 destroyed.
```

Run 2, `make down ZONE=us-central1-b` (started 2026-10-15T21:00:55Z, exit 0, 624 s):
```
data.terraform_remote_state.foundation: Read complete after 1s
module.gpu_pool.google_container_node_pool.this: Destruction complete after 21s
module.cluster.google_container_node_pool.system: Destruction complete after 4m53s
module.cluster.google_container_cluster.this: Destruction complete after 5m4s
Destroy complete! Resources: 3 destroyed.
container clusters:        none
compute instances:         none
compute disks:             none
compute forwarding-rules:  none
compute addresses:         none
nothing billable is running
```
