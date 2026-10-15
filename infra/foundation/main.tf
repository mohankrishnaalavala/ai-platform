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

# --- Deployer: what gpu-session.yml, cost-guard.yml and `make up/down` act as. Session work only.
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
  workflows          = ["gpu-session.yml", "cost-guard.yml"]
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
    "${var.project_id}-audit"   = { retention_seconds = 86400 } # P5 sets the real period and locks it
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
