terraform {
  # 1.5+ for import blocks; matched to dev's 1.7 minimum
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }

  # Same bucket as dev, different key. The apply role can write only
  # dev/terraform.tfstate, so CI can never tamper with this state.
  backend "s3" {
    bucket         = "ckerins-tfstate-12345"
    key            = "ci-iam/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}
