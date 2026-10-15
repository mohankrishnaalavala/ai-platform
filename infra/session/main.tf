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
}

module "gpu_pool" {
  source = "../modules/gpu-node-pool"

  cluster              = module.cluster.name
  zone                 = var.zone
  node_service_account = local.foundation.gke_nodes_email
  spot                 = var.gpu_spot
  node_labels          = local.labels
}
