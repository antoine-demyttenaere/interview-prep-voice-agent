# infra/budget.tf - an AWS Budget: emails you when the month's AWS bill heads past monthly_budget_usd ($10).
#
# A budget doesn't stop anything; it only warns. It emails alert_email when the actual spend
# reaches 80% of the budget, and when AWS forecasts the month will end above 100%. Budgets look
# at the whole account, so other projects in it count too. The first two budgets of an account
# are free. The paid APIs (Claude, speech) have their own budgets inside the app (app/costs/).
# AWS asks the address to confirm nothing: the alerts just arrive (check the spam folder once).

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
