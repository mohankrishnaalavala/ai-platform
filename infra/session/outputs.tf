output "cluster_name" {
  value = module.cluster.name
}

output "zone" {
  value = module.cluster.location
}

output "dns_endpoint" {
  value = module.cluster.dns_endpoint
}
