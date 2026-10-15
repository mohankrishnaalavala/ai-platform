variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "billing_account_id" {
  description = "Billing account linked to the project (holds the budget)."
  type        = string
}

variable "owner" {
  description = "Value of the owner label."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9_-]{0,62}$", var.owner))
    error_message = "owner must be a label value: lowercase letters, digits, - and _."
  }
}

variable "owner_email" {
  description = "Google account that may impersonate the deployer from the laptop."
  type        = string
}

variable "github_repo" {
  description = "owner/repo in the exact case GitHub uses (gh api repos/<repo> --jq .full_name)."
  type        = string
}

variable "region" {
  description = "Region for the subnet, buckets and Artifact Registry."
  type        = string
  default     = "us-central1"
}
