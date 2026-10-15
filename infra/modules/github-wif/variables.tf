variable "service_account_id" {
  description = "Full name of the service account the workflows act as (projects/<p>/serviceAccounts/<email>)."
  type        = string
}

variable "project_number" {
  description = "Number of the project that holds the WIF pool."
  type        = string
}

variable "pool_id" {
  description = "WIF pool created by infra/bootstrap."
  type        = string
  default     = "github"
}

variable "github_repo" {
  description = "owner/repo in the exact case GitHub uses (gh api repos/<repo> --jq .full_name)."
  type        = string
}

variable "workflows" {
  description = "Workflow file names in .github/workflows that may act as the service account."
  type        = list(string)
}
