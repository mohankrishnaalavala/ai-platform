# Lets named GitHub workflows act as one service account through the WIF pool that bootstrap
# created. This module never creates or changes the pool or its provider: they are how CI logs
# in, so a bad plan here must not be able to lock CI out.
resource "google_service_account_iam_member" "workflow" {
  for_each = toset(var.workflows)

  service_account_id = var.service_account_id
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${var.project_number}/locations/global/workloadIdentityPools/${var.pool_id}/attribute.workflow/${var.github_repo}/.github/workflows/${each.value}"
}
