# Seeded-bad plan for the Conftest policy: every shape of an SSH-from-the-world ingress rule,
# plus one rule whose CIDR is only known after apply. Plan only; never applied.
terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region = "us-east-1"
}

resource "aws_security_group" "inline" {
  name   = "seeded-inline"
  vpc_id = "vpc-0123456789abcdef0"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group_rule" "legacy" {
  type              = "ingress"
  security_group_id = "sg-0123456789abcdef0"
  from_port         = 20
  to_port           = 25
  protocol          = "tcp"
  ipv6_cidr_blocks  = ["::/0"]
}

resource "aws_vpc_security_group_ingress_rule" "all" {
  security_group_id = "sg-0123456789abcdef0"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc" "x" {
  cidr_block                       = "10.9.0.0/16"
  assign_generated_ipv6_cidr_block = true
}

# CIDR unknown until apply: warn, not deny
resource "aws_vpc_security_group_ingress_rule" "unknown" {
  security_group_id = "sg-0123456789abcdef0"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv6         = aws_vpc.x.ipv6_cidr_block
}

# Fine: HTTPS from the world
resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = "sg-0123456789abcdef0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}
