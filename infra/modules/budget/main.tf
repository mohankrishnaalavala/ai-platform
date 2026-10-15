# Email alerts only: a budget never stops spending. Emails go to the billing account's
# admins and users (the default recipients). Credits are excluded so they can't hide real spend.
resource "google_billing_budget" "this" {
  billing_account = var.billing_account_id
  display_name    = var.name

  budget_filter {
    projects               = ["projects/${var.project_number}"]
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.amount_usd)
    }
  }

  dynamic "threshold_rules" {
    for_each = var.alert_at_usd
    content {
      threshold_percent = threshold_rules.value / var.amount_usd
      spend_basis       = "CURRENT_SPEND"
    }
  }
}
