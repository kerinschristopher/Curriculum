# Unit tests for the vpc module: `terraform init -backend=false && terraform test` in modules/vpc.
#
# The AWS provider is mocked, so these need no credentials and create nothing: they check the
# module's logic (what it would create, wired to what), not that AWS accepts it. That's the job of
# the Terratest test in Acme/infra/terraform/test/, which builds the module in real AWS.
# CI runs these on every Terraform change (terraform-plan.yml, module-test job).
#
# `command = plan` checks values known before apply. `command = apply` against the mock is used
# where a check compares IDs, which only exist after apply (the mock invents them).

mock_provider "aws" {}

variables {
  name                 = "test"
  cidr                 = "10.0.0.0/16"
  azs                  = ["us-east-1a", "us-east-1b"]
  public_subnet_cidrs  = ["10.0.101.0/24", "10.0.102.0/24"]
  private_subnet_cidrs = ["10.0.32.0/19", "10.0.64.0/19"]
  enable_nat_gateway   = false
  tags = {
    Environment = "test"
    ManagedBy   = "terraform"
  }
}

# Dev runs with EKS off, and must cost nothing: no NAT gateway, no Elastic IP, no NAT route.
run "nat_off_creates_no_billed_resources" {
  command = plan

  assert {
    condition     = length(aws_nat_gateway.this) == 0 && length(aws_eip.nat) == 0
    error_message = "enable_nat_gateway = false must create no NAT gateway and no Elastic IP (both billed hourly)."
  }

  assert {
    condition     = length(aws_route.private_nat) == 0
    error_message = "enable_nat_gateway = false must create no private default route."
  }
}

run "nat_on_lives_in_a_public_subnet" {
  command = apply

  variables {
    enable_nat_gateway = true
  }

  assert {
    condition     = length(aws_nat_gateway.this) == 1 && length(aws_eip.nat) == 1
    error_message = "enable_nat_gateway = true must create exactly one NAT gateway and one Elastic IP."
  }

  assert {
    condition     = aws_nat_gateway.this[0].subnet_id == aws_subnet.public[0].id
    error_message = "The NAT gateway must sit in a public subnet, or private subnets get no internet."
  }

  assert {
    condition     = aws_route.private_nat[0].route_table_id == aws_route_table.private.id && aws_route.private_nat[0].nat_gateway_id == aws_nat_gateway.this[0].id
    error_message = "The private route table's default route must go through the NAT gateway."
  }
}

run "one_public_and_one_private_subnet_per_az" {
  command = plan

  assert {
    condition     = length(aws_subnet.public) == length(var.azs) && length(aws_subnet.private) == length(var.azs)
    error_message = "Expected one public and one private subnet per availability zone."
  }

  assert {
    condition     = alltrue(concat([for i, s in aws_subnet.public : s.availability_zone == var.azs[i]], [for i, s in aws_subnet.private : s.availability_zone == var.azs[i]]))
    error_message = "Subnet i must be in availability zone i."
  }

  assert {
    condition     = alltrue([for s in aws_subnet.public : s.map_public_ip_on_launch]) && !anytrue([for s in aws_subnet.private : s.map_public_ip_on_launch])
    error_message = "Only public subnets may give instances a public IP."
  }

  assert {
    condition     = alltrue([for s in aws_subnet.public : s.tags["kubernetes.io/role/elb"] == "1"]) && alltrue([for s in aws_subnet.private : s.tags["kubernetes.io/role/internal-elb"] == "1"])
    error_message = "Subnets need the kubernetes.io/role tags so EKS load balancers find them."
  }
}

run "public_subnets_route_to_the_internet_gateway" {
  command = apply

  assert {
    condition     = aws_route.public_internet.destination_cidr_block == "0.0.0.0/0" && aws_route.public_internet.gateway_id == aws_internet_gateway.this.id
    error_message = "The public route table needs a default route to the internet gateway."
  }

  assert {
    condition     = alltrue([for a in aws_route_table_association.public : a.route_table_id == aws_route_table.public.id])
    error_message = "Every public subnet must use the public route table."
  }

  assert {
    condition     = alltrue([for a in aws_route_table_association.private : a.route_table_id == aws_route_table.private.id])
    error_message = "Every private subnet must use the private route table, never the public one."
  }
}

# The CI apply role only creates resources whose request carries Environment = <env>
# (modules/iam-roles, Ec2CreateTagged). A resource that drops the caller's tags would plan cleanly
# and then fail with AccessDenied after the apply was approved.
run "every_taggable_resource_carries_the_callers_tags" {
  command = plan

  variables {
    enable_nat_gateway = true
  }

  assert {
    condition = alltrue([
      for t in concat(
        [aws_vpc.this.tags, aws_internet_gateway.this.tags, aws_route_table.public.tags, aws_route_table.private.tags],
        [for s in aws_subnet.public : s.tags],
        [for s in aws_subnet.private : s.tags],
        [for e in aws_eip.nat : e.tags],
        [for n in aws_nat_gateway.this : n.tags],
      ) : lookup(t, "Environment", "") == "test" && lookup(t, "ManagedBy", "") == "terraform"
    ])
    error_message = "Every taggable resource must carry the caller's tags (Environment is what the CI apply role checks)."
  }
}

run "rejects_a_single_availability_zone" {
  command = plan

  variables {
    azs                  = ["us-east-1a"]
    public_subnet_cidrs  = ["10.0.101.0/24"]
    private_subnet_cidrs = ["10.0.32.0/19"]
  }

  expect_failures = [var.azs]
}
