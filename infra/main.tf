# infra/main.tf - Terraform settings, the AWS provider, the inputs (variables) and the outputs of the app's AWS setup.
#
# Terraform reads every .tf file of this folder as one project ("module"), whatever the order:
#   main.tf      settings, variables, outputs (this file)
#   network.tf   the private network: VPC, public subnet, internet gateway, firewall
#   iam.tf       what the server may do in AWS (IAM role): Session Manager, SES, its SSM parameters
#   server.tf    the EC2 server, its disk, its fixed public IP, weekly snapshots (switch: server_enabled)
#   backup.tf    the S3 bucket for the nightly database backups (outlives the server)
#   domain.tf    the domain: DNS record to the server, SES sending (DKIM, SPF, DMARC), the
#                site address and sender as SSM settings
#   budget.tf    an email when the month's AWS bill heads past $10
#   github.tf    lets GitHub Actions deploy (OIDC, a role that can only run the deploy)
#   deploy.sh    runs on the server: builds .env from SSM, restores a backup into an empty
#                database, pulls the image, restarts the app
#   backup.sh    runs on the server, every night and before `server.ps1 down`: pg_dump to S3
#   server.ps1   your on/off switch: down (backup, then destroy the server) / up (rebuild, restore)
#   user_data.sh.tftpl  the script the server runs at its first boot (Docker, swap, the app)
# Secrets (API keys, SECRET_KEY, POSTGRES_PASSWORD) are NOT here: set-secrets.ps1 stores them in
# SSM Parameter Store, so they never appear in Terraform's files or state.
#
# First time (after infra/bootstrap):  terraform init -backend-config=backend.hcl
# Then each change:  terraform plan (shows what would change, free)  ->  terraform apply
# Your own values (alert email...) go in terraform.tfvars (git-ignored; see terraform.tfvars.example).

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  # The state lives in the S3 bucket made by infra/bootstrap; its name comes from backend.hcl.
  backend "s3" {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "interview-prep", ManagedBy = "terraform" }
  }
}

variable "region" {
  description = "AWS region: Ireland, the most complete EU region"
  type        = string
  default     = "eu-west-1"
}

variable "instance_type" {
  description = "t4g.micro: 2 ARM vCPUs, 1 GB memory (about $6.70 a month). t4g.small doubles memory and price."
  type        = string
  default     = "t4g.micro"
}

variable "server_enabled" {
  description = "true: the server runs (about $11.50 a month). false: the server, its disk and its IP are destroyed (about $0.50 a month left); the data waits in the S3 backup. Set by infra/server.ps1 in server.auto.tfvars."
  type        = bool
  default     = true
}

variable "alert_email" {
  description = "Where the AWS Budget emails its alerts (your own address; set it in terraform.tfvars)"
  type        = string
}

variable "monthly_budget_usd" {
  description = "AWS hosting budget per month, in US dollars (the paid APIs have their own, in the app)"
  type        = number
  default     = 10
}

variable "image_tag" {
  description = "Version of the app's image (ghcr.io/antoine-demyttenaere/interview-prep-voice-agent, arm64) the server pulls"
  type        = string
  default     = "latest"
}

locals {
  name       = "interview-prep"
  ssm_prefix = "/interview-prep/prod" # where set-secrets.ps1 stores the secrets
  app_dir    = "/opt/interview"       # on the server: docker-compose.prod.yml, Caddyfile, .env
}

# The outputs are null while the server is off (server_enabled = false).
output "public_ip" {
  description = "The server's fixed address: http://<this>/health"
  value       = one(aws_eip.server[*].public_ip)
}

output "instance_id" {
  value = one(aws_instance.server[*].id)
}

output "shell" {
  description = "Open a shell on the server (Session Manager, no SSH; needs the AWS CLI's session-manager-plugin)"
  value       = var.server_enabled ? "aws ssm start-session --target ${one(aws_instance.server[*].id)} --region ${var.region}" : null
}
