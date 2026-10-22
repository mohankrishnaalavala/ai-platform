# The persistent, cheap layer: network, service accounts, Artifact Registry, buckets, budget.
# Every IAM grant is additive (*_iam_member), so Terraform never removes bootstrap's grants or
# the owner's roles.

data "google_project" "this" {}

module "network" {
  source = "../modules/network"

  name          = "ai-platform"
  region        = var.region
  nodes_cidr    = "10.10.0.0/20"
  pods_cidr     = "10.20.0.0/16"
  services_cidr = "10.30.0.0/20"
}

# --- Deployer: what gpu-session.yml, cost-guard.yml, release-model.yml and `make up/down` act as.
# Session work only.
resource "google_service_account" "deployer" {
  account_id   = "deployer"
  display_name = "Deployer (sessions)"
  description  = "Creates and deletes the session cluster. No keys: workflows log in through WIF, the owner impersonates it."
}

resource "google_project_iam_member" "deployer_gke" {
  project = var.project_id
  role    = "roles/container.admin"
  member  = google_service_account.deployer.member
}

# Read-only: the Google provider lists instance groups when it reads node pools, and
# `make status` and cost-guard list instances, disks and addresses.
resource "google_project_iam_member" "deployer_compute_read" {
  project = var.project_id
  role    = "roles/compute.viewer"
  member  = google_service_account.deployer.member
}

resource "google_storage_bucket_iam_member" "deployer_state" {
  bucket = "${var.project_id}-tfstate"
  role   = "roles/storage.objectAdmin"
  member = google_service_account.deployer.member
}

resource "google_service_account_iam_member" "deployer_runs_nodes" {
  service_account_id = google_service_account.gke_nodes.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}

resource "google_service_account_iam_member" "owner_impersonates_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "user:${var.owner_email}"
}

module "deployer_wif" {
  source = "../modules/github-wif"

  service_account_id = google_service_account.deployer.name
  project_number     = data.google_project.this.number
  github_repo        = var.github_repo
  workflows          = ["gpu-session.yml", "cost-guard.yml", "release-model.yml"] # release-model.yml: P4's canary (ADR-0007)
}

# The session's Cloud Router and NAT. The custom role (router create/update/delete only) is
# created by bootstrap.sh, so the terraform account needs no role-admin rights.
resource "google_project_iam_member" "deployer_session_nat" {
  project = var.project_id
  role    = "projects/${var.project_id}/roles/sessionNat"
  member  = google_service_account.deployer.member
}

# Create-only: gpu-session.yml uploads its cold-start and scale-out timings, and can't overwrite.
resource "google_storage_bucket_iam_member" "deployer_results" {
  bucket = local.results_bucket
  role   = "roles/storage.objectCreator"
  member = google_service_account.deployer.member
}

# --- Mirror: what mirror.yml acts as. Copies the vLLM image and the model weights in, once.
resource "google_service_account" "mirror" {
  account_id   = "mirror"
  display_name = "Mirror (image and weights)"
  description  = "Copies the vLLM image into Artifact Registry and model weights into the models bucket. No keys: mirror.yml logs in through WIF."
}

resource "google_artifact_registry_repository_iam_member" "mirror_push" {
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.mirror.member
}

# Create and read, never overwrite or delete. Reading lets a re-run skip files already copied.
resource "google_storage_bucket_iam_member" "mirror_models" {
  for_each = toset(["roles/storage.objectCreator", "roles/storage.objectViewer"])

  bucket = local.models_bucket
  role   = each.value
  member = google_service_account.mirror.member
}

module "mirror_wif" {
  source = "../modules/github-wif"

  service_account_id = google_service_account.mirror.name
  project_number     = data.google_project.this.number
  github_repo        = var.github_repo
  workflows          = ["mirror.yml"]
}

# --- Pods in the session cluster, through GKE Workload Identity: IAM grants go straight to the
# Kubernetes service account (namespaces vllm, gateway and action-gateway), so no Google service account or key is involved.
# The Workload Identity pool these name exists once a session cluster has been created.
locals {
  # From the module's output, not typed out, so the grants wait until the buckets exist.
  models_bucket     = module.buckets.names[index(module.buckets.names, "${var.project_id}-models")]
  results_bucket    = module.buckets.names[index(module.buckets.names, "${var.project_id}-results")]
  audit_bucket      = module.buckets.names[index(module.buckets.names, "${var.project_id}-audit")]
  k8s_ns            = "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.project_id}.svc.id.goog/subject/ns"
  k8s_sa            = "${local.k8s_ns}/vllm/sa"
  litellm_sa        = "${local.k8s_ns}/gateway/sa/litellm"
  action_gateway_sa = "${local.k8s_ns}/action-gateway/sa/action-gateway"
}

resource "google_storage_bucket_iam_member" "vllm_reads_models" {
  bucket = local.models_bucket
  role   = "roles/storage.objectViewer"
  member = "${local.k8s_sa}/vllm"
}

resource "google_storage_bucket_iam_member" "bench_writes_results" {
  bucket = local.results_bucket
  role   = "roles/storage.objectCreator"
  member = "${local.k8s_sa}/bench"
}

# --- P2 gateway. Terraform creates the secrets but never a version: deploy.sh adds a random value
# each session, so no secret value is ever in Terraform state.
resource "google_secret_manager_secret" "gateway" {
  for_each = toset(["litellm-master-key", "gateway-postgres-password"])

  secret_id = each.key
  labels    = { layer = "02" }
  replication {
    auto {}
  }
}

# Add a version and destroy the previous one (destroyed versions stop billing); no read.
resource "google_secret_manager_secret_iam_member" "deployer_secret_versions" {
  for_each = google_secret_manager_secret.gateway

  secret_id = each.value.id
  role      = "roles/secretmanager.secretVersionManager"
  member    = google_service_account.deployer.member
}

# `make team` creates teams and keys through the gateway's admin API, which needs the master key. The
# deployer is both the laptop's identity and gpu-session.yml's, so the session tests can create keys too.
resource "google_secret_manager_secret_iam_member" "deployer_reads_master_key" {
  secret_id = google_secret_manager_secret.gateway["litellm-master-key"].id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.deployer.member
}

# LiteLLM reads both: the master key, and the Postgres password to build its database address.
resource "google_secret_manager_secret_iam_member" "litellm_reads_secrets" {
  for_each = google_secret_manager_secret.gateway

  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.litellm_sa
}

resource "google_secret_manager_secret_iam_member" "postgres_reads_password" {
  secret_id = google_secret_manager_secret.gateway["gateway-postgres-password"].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "${local.k8s_ns}/gateway/sa/postgres"
}

# Prompt requests to the hosted model (ADR-0009) and nothing else. bootstrap.sh creates the role.
resource "google_project_iam_member" "litellm_vertex_predict" {
  project = var.project_id
  role    = "projects/${var.project_id}/roles/vertexPredict"
  member  = local.litellm_sa
}

# --- P5 action gateway: Kubernetes service account action-gateway in namespace action-gateway.
# Secrets as for P2, containers only: the owner adds the two Slack token versions by hand, and P5's
# deploy.sh adds the action gateway's P2 team key each session.
resource "google_secret_manager_secret" "action_gateway" {
  for_each = toset(["slack-app-token", "slack-bot-token", "action-gateway-team-key"])

  secret_id = each.key
  labels    = { layer = "05" }
  replication {
    auto {}
  }
}

# The pod mounts all three through the GKE Secret Manager add-on.
resource "google_secret_manager_secret_iam_member" "action_gateway_reads_secrets" {
  for_each = google_secret_manager_secret.action_gateway

  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.action_gateway_sa
}

# deploy.sh adds this session's team key and destroys the previous one; no read. No grant on the Slack tokens.
resource "google_secret_manager_secret_iam_member" "deployer_team_key_versions" {
  secret_id = google_secret_manager_secret.action_gateway["action-gateway-team-key"].id
  role      = "roles/secretmanager.secretVersionManager"
  member    = google_service_account.deployer.member
}

# One object per audit record: create and read, never overwrite or delete (both need storage.objects.delete).
resource "google_storage_bucket_iam_member" "action_gateway_audit" {
  for_each = toset(["roles/storage.objectCreator", "roles/storage.objectViewer"])

  bucket = local.audit_bucket
  role   = each.value
  member = local.action_gateway_sa
}

# --- GKE nodes run as their own account, not the default Compute Engine one (which has Editor).
resource "google_service_account" "gke_nodes" {
  account_id   = "gke-nodes"
  display_name = "GKE nodes"
  description  = "Node pools of the session cluster: write logs and metrics, pull images from Artifact Registry."
}

resource "google_project_iam_member" "gke_nodes_default" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = google_service_account.gke_nodes.member
}

# GKE's image streaming docs require this for node accounts that aren't the Compute Engine default.
resource "google_project_iam_member" "gke_nodes_image_streaming" {
  project = var.project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = google_service_account.gke_nodes.member
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "images"
  format        = "DOCKER"
  description   = "Mirrored third-party images and our own images."
}

resource "google_artifact_registry_repository_iam_member" "gke_nodes_pull" {
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = google_service_account.gke_nodes.member
}

module "buckets" {
  source = "../modules/buckets"

  location = var.region
  buckets = {
    "${var.project_id}-models"  = { soft_delete = false } # weights are large and can be downloaded again
    "${var.project_id}-results" = {}
    "${var.project_id}-audit"   = { retention_seconds = 2592000 } # 30 days (P5). Not locked: a lock is permanent; locking is the production setting
  }
}

module "budget" {
  source = "../modules/budget"

  name               = "ai-platform"
  billing_account_id = var.billing_account_id
  project_number     = data.google_project.this.number
  amount_usd         = 50
  alert_at_usd       = [10, 20, 30]
}
