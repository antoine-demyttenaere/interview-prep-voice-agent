# infra/server.tf - the EC2 server that runs the app, its disk, its fixed public IP and its weekly snapshots.
#
# - AMI: the disk image the server starts from. Here the latest Amazon Linux 2023 for ARM
#   (Graviton) processors, looked up from AWS's public SSM parameter. A newer AMI is NOT applied
#   to a running server (ignore_changes): that would rebuild it and lose its data. The server
#   updates its own security packages instead (user data).
# - The server: t4g.micro, in the public subnet, behind the security group, with the IAM role.
#   "standard" CPU credits: when the CPU has been busy for long, it slows down rather than
#   billing extra (the default, "unlimited", could add a few dollars).
#   IMDSv2 required: the metadata service (where the role's credentials come from) answers only
#   requests holding a session token, which blocks a classic way of stealing them. Hop limit 2:
#   the app's container is one network hop further than the server, and must reach it for SES.
# - Disk: 10 GB gp3, encrypted, holding the system, Docker images and Postgres's data. Tagged
#   Snapshot=weekly, so Data Lifecycle Manager copies it every Sunday and keeps 4 copies.
# - Elastic IP: a public IPv4 address that stays the same when the server stops and starts
#   (the domain's DNS record points to it in step 3). Billed $3.65 a month like any IPv4.
# - user_data: the first-boot script (user_data.sh.tftpl), filled with the Compose file and the
#   Caddyfile of this repo. Editing it later doesn't touch the running server (deploys are step 4).

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_instance" "server" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.server.id]
  iam_instance_profile   = aws_iam_instance_profile.server.name

  credit_specification {
    cpu_credits = "standard"
  }

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    http_endpoint               = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 10
    encrypted   = true
    tags        = { Name = "${local.name}-disk", Snapshot = "weekly" }
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region       = var.region
    app_dir      = local.app_dir
    ssm_prefix   = local.ssm_prefix
    image_tag    = var.image_tag
    site_address = var.site_address
    mail_from    = var.mail_from
    compose_b64  = base64encode(file("${path.module}/../docker-compose.prod.yml"))
    caddy_b64    = base64encode(file("${path.module}/../Caddyfile"))
  })

  tags = { Name = local.name }

  lifecycle {
    ignore_changes = [ami, user_data]
  }
}

resource "aws_eip" "server" {
  domain   = "vpc"
  instance = aws_instance.server.id
  tags     = { Name = local.name }
}

resource "aws_dlm_lifecycle_policy" "weekly" {
  description        = "Weekly snapshot of the app server disk - 4 kept"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { Snapshot = "weekly" }

    schedule {
      name = "weekly-sunday"
      create_rule {
        cron_expression = "cron(0 3 ? * SUN *)" # Sundays at 03:00 UTC
      }
      retain_rule {
        count = 4
      }
      copy_tags = true
    }
  }
}
