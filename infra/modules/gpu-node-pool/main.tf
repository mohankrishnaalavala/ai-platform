# NVIDIA L4 pool that scales from 0, so no GPU VM exists until a pod asks for one.
# GKE installs the driver and adds the GPU taint and accelerator label itself.
resource "google_container_node_pool" "this" {
  name               = var.name
  cluster            = var.cluster
  location           = var.zone
  initial_node_count = 0

  autoscaling {
    min_node_count = 0 # never change: an idle GPU node is the biggest cost risk
    max_node_count = var.max_nodes
  }

  node_config {
    machine_type    = "g2-standard-8"
    spot            = var.spot
    service_account = var.node_service_account
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    resource_labels = var.node_labels

    guest_accelerator {
      type  = "nvidia-l4"
      count = 1
      gpu_driver_installation_config {
        gpu_driver_version = "DEFAULT"
      }
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    gcfs_config {
      enabled = true
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}
