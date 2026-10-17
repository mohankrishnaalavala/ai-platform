# The per-session cluster: exists only between `make up` and `make down`.
# Network, subnet and node service account come from the foundation state.

data "terraform_remote_state" "foundation" {
  backend = "gcs"
  config = {
    bucket = "${var.project_id}-tfstate"
    prefix = "foundation"
  }
}

locals {
  foundation = data.terraform_remote_state.foundation.outputs
  # Every us-central1 zone with L4 GPUs; the GPU pools may use any of them.
  l4_zones = ["us-central1-a", "us-central1-b", "us-central1-c"]
  # On every resource that takes labels, including node VMs and disks (make status, cost-guard).
  labels = {
    app     = "ai-platform"
    layer   = "00"
    owner   = var.owner
    session = var.session
  }
}

module "cluster" {
  source = "../modules/gke-cluster"

  name                 = "ai-platform"
  project_id           = var.project_id
  zone                 = var.zone
  gke_version          = "1.36.4-gke.1495002" # newest REGULAR version offered in us-central1-a, -b and -c, 2026-10-15
  network_id           = local.foundation.network_id
  subnet_id            = local.foundation.subnet_id
  pods_range_name      = local.foundation.pods_range_name
  services_range_name  = local.foundation.services_range_name
  node_service_account = local.foundation.gke_nodes_email
  node_labels          = local.labels
  autoscaling_profile  = var.autoscaling_profile
}

module "gpu_pool" {
  source = "../modules/gpu-node-pool"

  cluster              = module.cluster.name
  zone                 = var.zone
  node_locations       = local.l4_zones
  node_service_account = local.foundation.gke_nodes_email
  spot                 = var.gpu_spot
  node_labels          = local.labels
}

# For the baseline cold start only: GKE now streams public Docker Hub images too, so without
# this pool the "slow" baseline would quietly be half optimized. Both GPU pools look alike to the
# autoscaler, so every GPU pod must pick one with nodeSelector cloud.google.com/gke-nodepool.
module "gpu_baseline_pool" {
  source = "../modules/gpu-node-pool"

  name                 = "gpu-baseline"
  cluster              = module.cluster.name
  zone                 = var.zone
  node_locations       = local.l4_zones
  node_service_account = local.foundation.gke_nodes_email
  spot                 = var.gpu_spot
  max_nodes            = 1
  image_streaming      = false
  node_labels          = local.labels
}

# Outbound internet for the private nodes: Prometheus and KEDA images, and the baseline cold
# start (Docker Hub, Hugging Face). Google assigns the NAT IP; nothing can connect in.
# Routers and NATs take no labels, so `make status` lists routers.
resource "google_compute_router" "nat" {
  name    = "ai-platform-nat"
  region  = regex("^(.+)-[a-z]$", var.zone)[0]
  network = local.foundation.network_id
}

resource "google_compute_router_nat" "this" {
  name                               = "ai-platform-nat"
  router                             = google_compute_router.nat.name
  region                             = google_compute_router.nat.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}
