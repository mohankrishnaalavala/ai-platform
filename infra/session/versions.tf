terraform {
  required_version = "1.5.7" # last open-source (MPL-2.0) release

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.7.0"
    }
  }

  # terraform init -backend-config="bucket=<project>-tfstate"
  backend "gcs" {
    prefix = "session"
  }
}

# Credentials come only from GOOGLE_OAUTH_ACCESS_TOKEN: a short-lived token for the deployer
# service account. Never Application Default Credentials, never a key.
provider "google" {
  project = var.project_id
  region  = "us-central1"

  default_labels = local.labels
}
