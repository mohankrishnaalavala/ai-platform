variable "name" {
  description = "VPC name; the subnet is named <name>-<region>."
  type        = string
}

variable "region" {
  description = "Region of the subnet."
  type        = string
}

variable "nodes_cidr" {
  description = "Primary range of the subnet, used by nodes."
  type        = string
}

variable "pods_cidr" {
  description = "Secondary range for pods."
  type        = string
}

variable "services_cidr" {
  description = "Secondary range for Kubernetes services."
  type        = string
}
