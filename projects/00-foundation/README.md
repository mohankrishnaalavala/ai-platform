# P0: Foundation

A GPU sandbox on Google Cloud where nothing expensive exists until a session starts. CI logs in without keys, and a nightly job removes anything left running.

**Status:** day-1 setup in progress. Nothing is deployed yet. Idle cost: $[X] per month (not measured yet).

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

Still to do:

- Upgrade the billing account from the free trial. Trial accounts cannot use GPUs.
- Request GPU quota in `us-central1`: `GPUS_ALL_REGIONS` ≥ 2, `PREEMPTIBLE_NVIDIA_L4_GPUS` ≥ 2 and `NVIDIA_L4_GPUS` ≥ 1.
- Confirm which zones have L4 GPUs: `gcloud compute accelerator-types list --filter="name=nvidia-l4"`.
