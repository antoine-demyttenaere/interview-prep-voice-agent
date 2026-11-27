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
# - user_data: the first-boot script (user_data.sh.tftpl), filled with the Compose file, the
#   Caddyfile, deploy.sh and backup.sh of this repo. Editing it later doesn't touch the running
#   server; each deploy from GitHub brings the newest versions of those files.
#
# The on/off switch: server_enabled (main.tf). With false, Terraform destroys the server, its
# disk and its Elastic IP (count = 0), and keeps everything else: the network, the roles, the
# backup bucket, the domain's records, the budget. infra/server.ps1 down / up flips it, after a
# backup / before a restore. The data lives on in S3 (backup.tf), not on the destroyed disk.

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_instance" "server" {
  count                  = var.server_enabled ? 1 : 0
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

  # gzip-compressed (cloud-init unpacks it): the script and its four files are over AWS's 16 KB
  # limit for user data, about a third of it once compressed.
  user_data_base64 = base64gzip(templatefile("${path.module}/user_data.sh.tftpl", {
    region      = var.region
    app_dir     = local.app_dir
    image_tag   = var.image_tag
    compose_b64 = base64encode(file("${path.module}/../docker-compose.prod.yml"))
    caddy_b64   = base64encode(file("${path.module}/../Caddyfile"))
    deploy_b64  = base64encode(file("${path.module}/deploy.sh"))
    backup_b64  = base64encode(file("${path.module}/backup.sh"))
  }))

  tags = { Name = local.name }

  lifecycle {
    ignore_changes = [ami, user_data, user_data_base64]
  }
}

resource "aws_eip" "server" {
  count    = var.server_enabled ? 1 : 0
  domain   = "vpc"
  instance = aws_instance.server[0].id
  tags     = { Name = local.name }
}

# The server and its IP used to have no count: these tell Terraform they are the same resources
# as the first item of each list now, so adding the switch didn't rebuild anything.
moved {
  from = aws_instance.server
  to   = aws_instance.server[0]
}

moved {
  from = aws_eip.server
  to   = aws_eip.server[0]
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
