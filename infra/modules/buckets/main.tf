# Private, single-region buckets. Labels come from the provider's default_labels.
resource "google_storage_bucket" "this" {
  for_each = var.buckets

  name                        = each.key
  location                    = var.location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  dynamic "retention_policy" {
    for_each = each.value.retention_seconds == null ? [] : [each.value.retention_seconds]
    content {
      retention_period = retention_policy.value
      is_locked        = false # a locked policy can never be removed or shortened
    }
  }

  soft_delete_policy {
    retention_duration_seconds = each.value.soft_delete ? 604800 : 0 # 7 days is Google's default
  }
}
