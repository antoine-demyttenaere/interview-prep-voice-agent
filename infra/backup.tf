# infra/backup.tf - the private S3 bucket that keeps the database backups, so the server can be destroyed and rebuilt without losing data.
#
# The server's backup.sh (infra/backup.sh) saves the whole database with pg_dump (one file
# holding every table, the documents' vectors and the interview checkpoints) to
#   s3://interview-prep-backups-<account id>/interview/latest.dump   (the newest one)
#   s3://interview-prep-backups-<account id>/interview/daily/<date>.dump
# every night, and right before `server.ps1 down` destroys the server. On a new server,
# deploy.sh finds an empty database and loads latest.dump back (pg_restore) before the app starts.
#
# The bucket is not tied to the server: it stays when the server is switched off
# (server_enabled = false in main.tf). It is private, encrypted and refuses plain HTTP; daily
# copies are deleted after 30 days. Cost: a few cents a month (the dump is a few MB).
# Only the server's IAM role (iam.tf) and your admin login can read or write it.

resource "aws_s3_bucket" "backups" {
  bucket = "${local.name}-backups-${data.aws_caller_identity.me.account_id}"
  lifecycle {
    prevent_destroy = true # the backups must outlive everything else
  }
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration { status = "Enabled" } # an overwritten latest.dump can be recovered
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    id     = "expire-daily-copies"
    status = "Enabled"
    filter { prefix = "interview/daily/" }
    expiration { days = 30 }
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
  rule {
    id     = "expire-old-latest"
    status = "Enabled"
    filter { prefix = "interview/latest.dump" }
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
}

data "aws_iam_policy_document" "backups_https_only" {
  statement {
    sid       = "DenyPlainHttp"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.backups.arn, "${aws_s3_bucket.backups.arn}/*"]
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

resource "aws_s3_bucket_policy" "backups" {
  bucket     = aws_s3_bucket.backups.id
  policy     = data.aws_iam_policy_document.backups_https_only.json
  depends_on = [aws_s3_bucket_public_access_block.backups]
}

output "backup_bucket" {
  value = aws_s3_bucket.backups.bucket
}
