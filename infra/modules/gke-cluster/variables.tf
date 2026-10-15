variable "name" {
  description = "Cluster name."
  type        = string
}

variable "project_id" {
  description = "Project ID, for the Workload Identity pool name."
  type        = string
}

variable "zone" {
  description = "Zone of the cluster and its pools."
  type        = string
}

variable "gke_version" {
  description = "Exact GKE version to start with (recorded in docs/versions.md)."
  type        = string
}

variable "network_id" {
  description = "VPC from infra/foundation."
  type        = string
}

variable "subnet_id" {
  description = "Subnet from infra/foundation."
  type        = string
}

variable "pods_range_name" {
  description = "Secondary range for pods."
  type        = string
}

variable "services_range_name" {
  description = "Secondary range for services."
  type        = string
}

variable "node_service_account" {
  description = "Email of the service account every node runs as."
  type        = string
}

variable "system_machine_type" {
  description = "Machine type of the 1-node system pool."
  type        = string
  default     = "e2-standard-4"
}

variable "node_labels" {
  description = "Labels on the node VMs and their boot disks (app, layer, owner, session)."
  type        = map(string)
}
