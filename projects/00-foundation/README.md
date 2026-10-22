# P0: Foundation

A GPU sandbox on Google Cloud where nothing expensive exists until a session starts. CI logs in without keys, and a nightly job removes anything left running.

**Status:** bootstrap and foundation done. The session cluster has made two clean round trips (`make up` 11m39s and 11m56s, `make down` 10m18s and 10m24s; [results](results/round-trip-2026-10-15.md)). A spot L4 node has started from zero with Google-managed drivers and no NAT, and the nightly cleanup job has destroyed a deliberately forgotten cluster. Laptop mode is not part of P0. Idle cost: $[X] per month (not measured yet).

## Day-1 setup (manual, one time)

Run on macOS. Replace `<PROJECT_ID>`, `<BILLING_ACCOUNT_ID>` and `<you>` with your own values.

1. **Tools.** You need Terraform, the gcloud CLI, kubectl, Helm, Docker (for the linters), jq and the GitHub CLI.

   ```bash
   gcloud components install gke-gcloud-auth-plugin   # kubectl needs this to reach GKE
   ```

   If `gke-gcloud-auth-plugin` is still not found afterwards, add the gcloud SDK `bin` folder to your `PATH`.

2. **A separate gcloud configuration.** It keeps this project's commands away from any other project on the machine.

   ```bash
   gcloud config configurations create ai-platform --no-activate
   export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform
   gcloud auth login
   ```

3. **The project.**

   ```bash
   gcloud projects create <PROJECT_ID> --name=ai-platform --labels=app=ai-platform,layer=00,owner=<you>
   gcloud config set project <PROJECT_ID>
   gcloud billing projects link <PROJECT_ID> --billing-account=<BILLING_ACCOUNT_ID>
   ```

4. **Bootstrap** (once; safe to run again). It turns on the APIs, deletes the `default` network (its firewall rules allow SSH from anywhere), and creates the Terraform state bucket, a `terraform` service account and keyless login for GitHub Actions. No service account key is ever created.

   ```bash
   cp infra/bootstrap/.env.example infra/bootstrap/.env   # fill in your values; .env is gitignored
   infra/bootstrap/bootstrap.sh
   ```

   It stops before changing anything if the `ai-platform` configuration points at another project or account, or the billing account is not the project's. A second run only prints "skipped". If the `default` network ever comes back, a re-run deletes it again.

5. **Foundation** (Terraform). It creates the network, the `deployer` and `gke-nodes` service accounts, Artifact Registry, three buckets and a $50 monthly budget with email alerts at $10, $20 and $30. A budget only sends emails; it never stops spending.

   ```bash
   export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform
   unset GOOGLE_APPLICATION_CREDENTIALS GOOGLE_CREDENTIALS   # never use machine-wide credentials
   P=$(gcloud config get project)
   # Terraform logs in only with this one-hour token for the terraform service account. No key.
   export GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token --impersonate-service-account=terraform@$P.iam.gserviceaccount.com)"
   cd infra/foundation
   cp terraform.tfvars.example terraform.tfvars   # fill in your values; gitignored
   terraform init -backend-config="bucket=$P-tfstate"
   terraform plan -out=foundation.tfplan
   terraform apply foundation.tfplan
   ```

   Every IAM grant only adds, so Terraform never removes bootstrap's grants or your own roles. `make foundation-plan` and `make foundation-apply` run the same steps and read your values from gcloud and GitHub, so no `terraform.tfvars` is needed. After the first apply, the `foundation.yml` workflow takes over (P0-13).

6. **GPUs.** Upgrade the billing account from the free trial (trial accounts can't use GPUs), then request quota in the console or the Cloud Quotas API. `gcloud compute accelerator-types list --filter="name:nvidia-l4"` shows which zones have L4 GPUs. A new account may be refused a bigger request but granted `GPUS_ALL_REGIONS` = 1, which is enough for one L4 (spot or on demand).

7. **A session.** Everything that costs money lives only between `up` and `down`.

   ```bash
   make up                  # the session cluster (11m39s–11m56s measured); ZONE=us-central1-b for another zone
   make test P=00           # nodes Ready, system pods running, no external IPs, no service account keys
   make down                # deletes PVCs, destroys the cluster, then runs make status
   make status              # must say "nothing billable is running"
   ```

   L4 capacity runs out in a zone at times (`ZONE_RESOURCE_POOL_EXHAUSTED`). Try another zone, or on demand with `gpu_spot = false`.

8. **Workflows.** Set the two repository secrets listed in [`.github/workflows/README.md`](../../.github/workflows/README.md). `gpu-session.yml` runs a session from GitHub; `cost-guard.yml` destroys any session cluster older than 8 hours every night and opens an issue. To keep a cluster up for days, set the repository variable `COST_GUARD_MAX_AGE_HOURS` (at most 168) and delete it afterwards.
