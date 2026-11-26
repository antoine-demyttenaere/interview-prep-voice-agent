# infra/bootstrap/main.tf - creates, once, the S3 bucket where Terraform keeps the "state" of infra/.
#
# Terraform's state is a JSON file listing every AWS resource it created and their ids, so the
# next `terraform apply` knows what already exists. Kept on your laptop it could be lost or
# out of date; kept in S3 it is safe, versioned (every old copy is kept, so a broken state can
# be rolled back) and locked while someone runs Terraform (S3 locking: a lock file next to it).
# The bucket can't hold infra/'s own state before it exists, so this small folder creates it
# with a local state (terraform.tfstate here, git-ignored; it only lists this bucket).
#
# The bucket is private (public access blocked), encrypted, refuses plain-HTTP access, and
# survives `terraform destroy` of infra/. Its name includes your AWS account id (unique
# worldwide), and this folder writes ../backend.hcl (git-ignored) so infra/ finds it.
#
# Run once, from infra/bootstrap:  terraform init  then  terraform apply
# Costs: a few cents a year (the state is a few KB).

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws   = { source = "hashicorp/aws", version = "~> 6.0" }
    local = { source = "hashicorp/local", version = "~> 2.5" }
  }
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "interview-prep", ManagedBy = "terraform-bootstrap" }
  }
}

data "aws_caller_identity" "me" {}

resource "aws_s3_bucket" "state" {
  bucket = "interview-prep-tfstate-${data.aws_caller_identity.me.account_id}"
  lifecycle {
    prevent_destroy = true # losing the state would make Terraform forget the server
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule { object_ownership = "BucketOwnerEnforced" } # no ACLs: only IAM decides who reads it
}

# Old versions of the state are deleted after 90 days, so the bucket doesn't grow forever.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 90 }
  }
}

data "aws_iam_policy_document" "https_only" {
  statement {
    sid       = "DenyPlainHttp"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket     = aws_s3_bucket.state.id
  policy     = data.aws_iam_policy_document.https_only.json
  depends_on = [aws_s3_bucket_public_access_block.state]
}

# infra/ reads this with `terraform init -backend-config=backend.hcl`.
resource "local_file" "backend_config" {
  filename = "${path.module}/../backend.hcl"
  content  = <<-EOT
    # backend.hcl - where infra/ keeps its Terraform state (written by infra/bootstrap; git-ignored).
    bucket       = "${aws_s3_bucket.state.bucket}"
    key          = "interview-prep/terraform.tfstate"
    region       = "${var.region}"
    encrypt      = true
    use_lockfile = true
  EOT
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}
