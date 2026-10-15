# P0: Foundation

A GPU sandbox on Google Cloud where nothing expensive exists until a session starts. CI logs in without keys, and a nightly job removes anything left running.

**Status:** bootstrap and foundation done: network, service accounts, Artifact Registry, buckets and budget exist. No cluster yet. Idle cost: $[X] per month (not measured yet).

## Day-1 setup (manual, one time)

Run on macOS. Replace `<PROJECT_ID>`, `<BILLING_ACCOUNT_ID>` and `<you>` with your own values.

1. **Tools.** You need Terraform, the gcloud CLI, kubectl, Helm, Docker, kind, jq and the GitHub CLI.

   ```bash
   brew install kind
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

   Every IAM grant only adds, so Terraform never removes bootstrap's grants or your own roles. `make` wraps these steps in P0-06, and after the first apply the `foundation.yml` workflow takes over (P0-13).

Still to do:

- Upgrade the billing account from the free trial. Trial accounts cannot use GPUs.
- Request GPU quota in `us-central1` (`gcloud compute accelerator-types list --filter="name:nvidia-l4"` shows which zones have L4 GPUs): `GPUS_ALL_REGIONS` ≥ 2, `PREEMPTIBLE_NVIDIA_L4_GPUS` ≥ 2 and `NVIDIA_L4_GPUS` ≥ 1.
