# NVIDIA L4 pool that scales from 0, so no GPU VM exists until a pod asks for one. Its nodes may
# start in any of node_locations: spot L4s run out zone by zone, so the autoscaler takes a free one.
# GKE installs the driver and adds the accelerator label itself.
resource "google_container_node_pool" "this" {
  name               = var.name
  cluster            = var.cluster
  location           = var.zone
  node_locations     = var.node_locations
  initial_node_count = 0

  # Totals across the zones (the per-zone min/max would allow one node per zone).
  autoscaling {
    total_min_node_count = 0 # never change: an idle GPU node is the biggest cost risk
    total_max_node_count = var.max_nodes
    location_policy      = "ANY" # any zone with a GPU free, not spread evenly
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
      enabled = var.image_streaming
    }

    # Declared here, not left to GKE: the autoscaler plans from the pool's config, and without
    # the taint it started a GPU node for kube-dns. Pods that request a GPU tolerate it automatically.
    taint {
      key    = "nvidia.com/gpu"
      value  = "present"
      effect = "NO_SCHEDULE"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}
