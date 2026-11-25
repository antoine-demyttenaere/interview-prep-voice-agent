# infra/main.tf - Terraform settings, the AWS provider, the inputs (variables) and the outputs of the app's AWS setup.
#
# Terraform reads every .tf file of this folder as one project ("module"), whatever the order:
#   main.tf      settings, variables, outputs (this file)
#   network.tf   the private network: VPC, public subnet, internet gateway, firewall
#   iam.tf       what the server may do in AWS (IAM role): Session Manager, SES, its SSM parameters
#   server.tf    the EC2 server, its disk, its fixed public IP, weekly snapshots
#   budget.tf    an email when the month's AWS bill heads past $10
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

variable "alert_email" {
  description = "Where the AWS Budget emails its alerts (your own address; set it in terraform.tfvars)"
  type        = string
}

variable "monthly_budget_usd" {
  description = "AWS hosting budget per month, in US dollars (the paid APIs have their own, in the app)"
  type        = number
  default     = 10
}

variable "site_address" {
  description = "What Caddy serves: \":80\" (plain HTTP on the IP, until a domain exists), then the domain (step 3)"
  type        = string
  default     = ":80"
}

variable "mail_from" {
  description = "Sender of the login emails; its domain must be verified in SES (step 3)"
  type        = string
  default     = "Practiss <no-reply@example.com>"
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

output "public_ip" {
  description = "The server's fixed address: http://<this>/health"
  value       = aws_eip.server.public_ip
}

output "instance_id" {
  value = aws_instance.server.id
}

output "shell" {
  description = "Open a shell on the server (Session Manager, no SSH; needs the AWS CLI's session-manager-plugin)"
  value       = "aws ssm start-session --target ${aws_instance.server.id} --region ${var.region}"
}
