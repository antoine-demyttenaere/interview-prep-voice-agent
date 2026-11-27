# infra/domain.tf - the app's domain (practissinterview.com): its DNS record to the server, and Amazon SES set up to send email from it.
#
# The domain is registered by hand in the Route 53 console (it needs your card and contact
# details), which also creates its "hosted zone": the place where the domain's DNS records live
# ($0.50 a month). Terraform finds that zone and adds the records below to it.
#
# - A record: practissinterview.com -> the server's Elastic IP. Caddy then gets the HTTPS
#   certificate from Let's Encrypt by itself. The record exists only while the server does
#   (server_enabled), and follows the new IP after `server.ps1 up`.
# - SES domain identity: lets the server send email from any address @practissinterview.com.
#   Mail providers (Gmail, Outlook) trust an email more when the domain proves it sent it:
#   - DKIM: SES signs each email; the 3 CNAME records publish the keys that check the signature.
#   - SPF and a custom MAIL FROM (mail.practissinterview.com): say that SES may send for us.
#   - DMARC: tells providers what to do with an email that fails both (here: report only).
#   Without them, login codes would often land in spam.
# - SSM parameters SITE_ADDRESS and MAIL_FROM: deploy.sh puts them in the server's .env, where
#   they replace the first-boot defaults (settings.env). Not secret, so Terraform can own them.
#
# A new SES account is in the "sandbox": it can only send to verified addresses, 200 a day,
# until AWS approves a request to leave it (Phase 5 step 3, sent from the console or the CLI).

variable "domain" {
  description = "The app's domain, registered in Route 53 (its hosted zone must exist)"
  type        = string
  default     = "practissinterview.com"
}

locals {
  mail_from_domain = "mail.${var.domain}"
}

data "aws_route53_zone" "main" {
  name = var.domain
}

resource "aws_route53_record" "site" {
  count   = var.server_enabled ? 1 : 0
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.domain
  type    = "A"
  ttl     = 300
  records = [aws_eip.server[0].public_ip]
}

# --- Amazon SES: send email from the domain ---

resource "aws_sesv2_email_identity" "domain" {
  email_identity = var.domain
}

resource "aws_route53_record" "dkim" {
  count   = 3 # SES "Easy DKIM" always gives 3 keys
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "${aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens[count.index]}._domainkey.${var.domain}"
  type    = "CNAME"
  ttl     = 1800
  records = ["${aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"]
}

resource "aws_sesv2_email_identity_mail_from_attributes" "domain" {
  email_identity         = aws_sesv2_email_identity.domain.email_identity
  mail_from_domain       = local.mail_from_domain
  behavior_on_mx_failure = "USE_DEFAULT_VALUE"
}

resource "aws_route53_record" "mail_from_mx" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = local.mail_from_domain
  type    = "MX"
  ttl     = 1800
  records = ["10 feedback-smtp.${var.region}.amazonses.com"]
}

resource "aws_route53_record" "mail_from_spf" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = local.mail_from_domain
  type    = "TXT"
  ttl     = 1800
  records = ["v=spf1 include:amazonses.com ~all"]
}

resource "aws_route53_record" "dmarc" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "_dmarc.${var.domain}"
  type    = "TXT"
  ttl     = 1800
  records = ["v=DMARC1; p=none;"]
}

# --- The app's settings for this domain (read by deploy.sh) ---

resource "aws_ssm_parameter" "site_address" {
  name  = "${local.ssm_prefix}/SITE_ADDRESS"
  type  = "String"
  value = var.domain
}

resource "aws_ssm_parameter" "mail_from" {
  name  = "${local.ssm_prefix}/MAIL_FROM"
  type  = "String"
  value = "Practiss <no-reply@${var.domain}>"
}

output "site_url" {
  value = "https://${var.domain}"
}
