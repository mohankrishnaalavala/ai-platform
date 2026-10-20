# Zonal GKE Standard cluster with private nodes. The control plane is reachable only through its
# DNS-based endpoint, where IAM decides who gets in (no IP allowlist). Includes the system pool.
# Authorized networks only restrict the IP endpoints, which are disabled below. The DNS endpoint
# is gated by IAM (container.clusters.connect), so there is nothing for them to restrict.
#trivy:ignore:GCP-0061
resource "google_container_cluster" "this" {
  name       = var.name
  location   = var.zone
  network    = var.network_id
  subnetwork = var.subnet_id

  min_master_version  = var.gke_version
  deletion_protection = false # `make down` must be able to destroy it

  release_channel {
    channel = "REGULAR"
  }

  # GKE always creates a default pool first. Make it run as our node account (the deployer may
  # only act as that one), then remove it in favour of the pools below.
  remove_default_node_pool = true
  initial_node_count       = 1
  node_config {
    service_account = var.node_service_account
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = var.pods_range_name
    services_secondary_range_name = var.services_range_name
  }

  private_cluster_config {
    enable_private_nodes = true
  }

  control_plane_endpoints_config {
    dns_endpoint_config {
      allow_external_traffic = true
    }
    ip_endpoints_config {
      enabled = false
    }
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # GKE Dataplane V2 enforces Kubernetes NetworkPolicies (P3's game day blocks the gateway's calls to the
  # hosted model with one). Free; set when the cluster is created (changing it replaces the cluster).
  datapath_provider = "ADVANCED_DATAPATH"

  addons_config {
    gcs_fuse_csi_driver_config {
      enabled = true
    }
  }

  secret_manager_config {
    enabled = true
  }

  # Only the profile: node auto-provisioning stays off, the pools autoscale on their own.
  cluster_autoscaling {
    autoscaling_profile = var.autoscaling_profile
  }

  lifecycle {
    ignore_changes = [node_config] # only describes the removed default pool
  }
}

resource "google_container_node_pool" "system" {
  name       = "system"
  cluster    = google_container_cluster.this.name
  location   = var.zone
  node_count = 1

  node_config {
    machine_type    = var.system_machine_type
    service_account = var.node_service_account
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"] # IAM decides what nodes can do
    resource_labels = var.node_labels

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    gcfs_config {
      enabled = true # image streaming
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}
