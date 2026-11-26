# infra/github.tf - lets GitHub Actions deploy to the server without any AWS key stored in GitHub (OIDC).
#
# How it works: for each run, GitHub signs a short "ID token" saying which repository, branch
# and workflow is running (OpenID Connect, OIDC). The OIDC provider below tells AWS to trust
# tokens signed by GitHub. The workflow (.github/workflows/deploy.yml) hands its token to AWS
# STS, which gives back credentials of the github-deploy role for one hour.
#
# The role's trust policy accepts only tokens from THIS repository's main branch (the token's
# "sub", e.g. repo:owner@id/name@id:ref:refs/heads/main), so another repository, a fork or
# another branch can't use it. A refused login shows the "sub" GitHub sent in CloudTrail. And the role can only do one thing: run
# a shell command on this server through SSM Run Command (the deploy), and read its result.
# It can't read the secrets, change the infrastructure or touch anything else in the account.
#
# After `terraform apply`, the workflow needs two repository variables (not secrets: neither is
# sensitive): AWS_DEPLOY_ROLE_ARN = the deploy_role_arn output, AWS_INSTANCE_ID = instance_id.

variable "github_repo" {
  description = "The GitHub repository allowed to deploy, as GitHub writes it in its tokens: owner@owner-id/name@repo-id"
  type        = string
  # GitHub's tokens name the owner and the repository with their numeric ids (seen in CloudTrail,
  # AssumeRoleWithWebIdentity events). The ids never change and are never reused, so a repository
  # deleted and re-created under the same name by someone else could not deploy.
  default = "antoine-demyttenaere@109357504/interview-prep-voice-agent@1469022852"
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_can_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name                 = "${local.name}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.github_can_assume.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid     = "RunCommandOnTheServer"
    actions = ["ssm:SendCommand"]
    resources = [
      aws_instance.server.arn,
      "arn:aws:ssm:${var.region}::document/AWS-RunShellScript",
    ]
  }
  statement {
    sid       = "ReadTheResult" # these two actions can't be limited to one server
    actions   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "deploy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}

output "deploy_role_arn" {
  description = "Repository variable AWS_DEPLOY_ROLE_ARN for the deploy workflow"
  value       = aws_iam_role.github_deploy.arn
}
