variable "name" {
  description = "Node pool name."
  type        = string
  default     = "gpu"
}

variable "cluster" {
  description = "Cluster name."
  type        = string
}

variable "zone" {
  description = "Zone of the cluster."
  type        = string
}

variable "node_service_account" {
  description = "Email of the service account the nodes run as."
  type        = string
}

variable "spot" {
  description = "Spot VMs (cheaper, can be taken away). Set false if spot capacity runs out."
  type        = bool
}

variable "max_nodes" {
  description = "Autoscaling upper bound."
  type        = number
  default     = 2
}

variable "node_labels" {
  description = "Labels on the node VMs and their boot disks (app, layer, owner, session)."
  type        = map(string)
}

variable "image_streaming" {
  description = "Stream container images instead of pulling them whole. Off only for the baseline cold start."
  type        = bool
  default     = true
}

variable "node_locations" {
  description = "Zones the pool's nodes may start in (same region as the cluster)."
  type        = list(string)
}
