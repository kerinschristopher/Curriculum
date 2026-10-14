# Tests for the dev environment: `terraform test` in environments/dev.
#
# The AWS provider is mocked, so these need no credentials and create nothing.
# They check that enable_eks is fixed in the committed code: nothing from outside
# (a tfvars file, -var, TF_VAR_) can switch EKS on. See review comment #4.

mock_provider "aws" {
  # The mock invents random strings, but the EKS module checks that these are a real ARN and real JSON.
  # Only used while EKS is in the plan.
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
      arn        = "arn:aws:iam::123456789012:user/test"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition  = "aws"
      dns_suffix = "amazonaws.com"
    }
  }
}

run "eks_off_by_default" {
  command = plan

  assert {
    condition     = length(module.eks) == 0
    error_message = "EKS must be off unless the committed code turns it on."
  }
}

# Simulates `enable_eks = true` in dev.auto.tfvars. Terraform warns "undeclared variable" and ignores it.
run "cannot_be_switched_on_from_outside" {
  command = plan

  variables {
    enable_eks              = true
    eks_public_access_cidrs = ["203.0.113.7/32"]
  }

  assert {
    condition     = length(module.eks) == 0
    error_message = "A value from outside the code switched EKS on. Only a code change may do that."
  }
}
