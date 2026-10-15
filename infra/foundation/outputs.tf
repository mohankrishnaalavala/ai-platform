# What infra/session (P0-05) and the Makefile (P0-06) need.
output "network_id" {
  value = module.network.network_id
}

output "subnet_id" {
  value = module.network.subnet_id
}

output "pods_range_name" {
  value = module.network.pods_range_name
}

output "services_range_name" {
  value = module.network.services_range_name
}

output "deployer_email" {
  value = google_service_account.deployer.email
}

output "gke_nodes_email" {
  value = google_service_account.gke_nodes.email
}

output "images_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "buckets" {
  value = module.buckets.names
}
