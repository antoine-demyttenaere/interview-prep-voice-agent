# infra/network.tf - the server's private network in AWS, and its firewall (security group).
#
# - VPC (Virtual Private Cloud): a private network of our own inside AWS, 10.0.0.0/16.
# - Subnet: a slice of it in one data center ("availability zone"). It is "public" because its
#   route table sends internet traffic (0.0.0.0/0) to an internet gateway, the VPC's door to
#   the internet. One subnet is enough for one server.
# - Security group: a firewall around the server. It lets in only the web ports, 80 (HTTP, which
#   Caddy redirects to HTTPS) and 443 (HTTPS, also over UDP for HTTP/3). There is no SSH port:
#   shells go through Session Manager, which needs no open port. Everything may go out (Claude,
#   Deepgram, package updates, Session Manager).

data "aws_availability_zones" "here" {
  state = "available"
}

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = data.aws_availability_zones.here.names[0]
  tags              = { Name = "${local.name}-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "server" {
  name        = "${local.name}-server"
  description = "Web ports only (80, 443); no SSH, shells use Session Manager"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.name}-server" }
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.server.id
  description       = "HTTP (Caddy redirects it to HTTPS; the Lets Encrypt checks use it)"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.server.id
  description       = "HTTPS"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "http3" {
  security_group_id = aws_security_group.server.id
  description       = "HTTP/3 (HTTPS over UDP)"
  ip_protocol       = "udp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "all_out" {
  security_group_id = aws_security_group.server.id
  description       = "Everything out: the paid APIs, updates, Session Manager, SES"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
