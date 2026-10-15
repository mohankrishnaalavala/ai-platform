variable "name" {
  description = "Budget display name."
  type        = string
}

variable "billing_account_id" {
  description = "Billing account that holds the budget."
  type        = string
}

variable "project_number" {
  description = "The only project the budget counts."
  type        = string
}

variable "amount_usd" {
  description = "Monthly budget in US dollars."
  type        = number

  validation {
    condition     = var.amount_usd > 0
    error_message = "amount_usd must be greater than 0."
  }
}

variable "alert_at_usd" {
  description = "Spend amounts, in US dollars, that send an alert email."
  type        = list(number)

  validation {
    condition     = length(var.alert_at_usd) > 0
    error_message = "Give at least one alert amount."
  }
}
