variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "owner" {
  description = "Value of the owner label."
  type        = string
}

variable "session" {
  description = "Session label, e.g. $(date -u +%Y%m%dt%H%Mz). cost-guard uses it to find old clusters."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{8}t[0-9]{4}z$", var.session))
    error_message = "session must look like 20261016t1405z (date -u +%Y%m%dt%H%Mz)."
  }
}

variable "zone" {
  description = "Zone with L4 GPUs. If spot capacity runs out, try another."
  type        = string
  default     = "us-central1-a"

  validation {
    condition     = can(regex("^us-central1-[abc]$", var.zone))
    error_message = "zone must be us-central1-a, -b or -c (the zones with L4 GPUs)."
  }
}

variable "gpu_spot" {
  description = "Use spot VMs for the GPU pool. Set false if spot capacity isn't available."
  type        = bool
  default     = true
}
