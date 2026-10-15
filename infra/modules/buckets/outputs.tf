output "names" {
  value = keys(google_storage_bucket.this)
}
