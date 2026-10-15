terraform {
  # 1.5.7 is the last open-source (MPL-2.0) Terraform release.
  required_version = "1.5.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.7.0"
    }
  }

  # The bucket comes from bootstrap: terraform init -backend-config="bucket=<project>-tfstate"
  backend "gcs" {
    prefix = "foundation"
  }
}

# Credentials come only from GOOGLE_OAUTH_ACCESS_TOKEN: a short-lived token for the terraform
# service account (see the P0 README). Never Application Default Credentials, never a key.
provider "google" {
  project = var.project_id
  region  = var.region

  default_labels = {
    app   = "ai-platform"
    layer = "00"
    owner = var.owner
  }
}
