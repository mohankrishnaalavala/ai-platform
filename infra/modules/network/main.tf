# Our own VPC instead of the default network. No firewall rules: a VPC's implied rules deny all
# inbound traffic. Nodes have no public IPs and reach Google APIs through Private Google Access.
resource "google_compute_network" "this" {
  name                    = var.name
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "this" {
  name                     = "${var.name}-${var.region}"
  network                  = google_compute_network.this.id
  region                   = var.region
  ip_cidr_range            = var.nodes_cidr
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pods_cidr
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.services_cidr
  }
}
