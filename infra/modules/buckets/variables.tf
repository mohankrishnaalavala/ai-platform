variable "location" {
  description = "Region for every bucket."
  type        = string
}

variable "buckets" {
  description = "Bucket name => options. retention_seconds adds an unlocked retention policy; soft_delete = false stops deleted objects from billing for 7 more days."
  type = map(object({
    retention_seconds = optional(number)
    soft_delete       = optional(bool, true)
  }))
}
