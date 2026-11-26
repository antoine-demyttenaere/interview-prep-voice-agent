# infra/iam.tf - what the server and the snapshot service may do in AWS (IAM roles), and nothing more.
#
# An IAM role is a set of permissions given to a machine or an AWS service instead of a person.
# The server gets its role through an "instance profile"; programs on it (the AWS CLI, boto3 in
# the app) then receive short-lived credentials from the instance metadata service, so no AWS
# key is ever stored on the server or in the repo.
#
# The server's role may:
# - be managed by Systems Manager (AmazonSSMManagedInstanceCore): Session Manager shells, and
#   SSM Run Command for deploys (step 4);
# - send email with SES (app/mail SesMailer), from identities of this account only;
# - write and read the database backups in the backup bucket (backup.tf), never delete them;
# - read its own secrets under /interview-prep/prod in SSM Parameter Store (set-secrets.ps1),
#   decrypted with the default AWS key for SSM.
# The snapshot service (Data Lifecycle Manager, server.tf) gets AWS's own role for snapshots.

data "aws_caller_identity" "me" {}

data "aws_iam_policy_document" "ec2_can_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "server" {
  name               = "${local.name}-server"
  assume_role_policy = data.aws_iam_policy_document.ec2_can_assume.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.server.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "server" {
  statement {
    sid       = "SendEmail"
    actions   = ["ses:SendEmail", "ses:SendRawEmail"]
    resources = ["arn:aws:ses:${var.region}:${data.aws_caller_identity.me.account_id}:identity/*"]
  }
  statement {
    sid       = "ListBackups"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.backups.arn]
  }
  statement {
    sid       = "ReadWriteBackups" # backup.sh writes the dumps, deploy.sh reads one back
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.backups.arn}/*"]
  }
  statement {
    sid     = "ReadOwnSecrets"
    actions = ["ssm:GetParametersByPath", "ssm:GetParameters", "ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${var.region}:${data.aws_caller_identity.me.account_id}:parameter${local.ssm_prefix}",
      "arn:aws:ssm:${var.region}:${data.aws_caller_identity.me.account_id}:parameter${local.ssm_prefix}/*",
    ]
  }
}

resource "aws_iam_role_policy" "server" {
  name   = "app"
  role   = aws_iam_role.server.id
  policy = data.aws_iam_policy_document.server.json
}

resource "aws_iam_instance_profile" "server" {
  name = "${local.name}-server"
  role = aws_iam_role.server.name
}

# --- Data Lifecycle Manager (weekly snapshots of the server's disk) ---

data "aws_iam_policy_document" "dlm_can_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dlm" {
  name               = "${local.name}-snapshots"
  assume_role_policy = data.aws_iam_policy_document.dlm_can_assume.json
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}
