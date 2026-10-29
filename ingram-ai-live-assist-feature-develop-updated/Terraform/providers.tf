locals {
  # Required by the organisation's conftest policy (configuration.json: required_tags).
  required_tags = {
    owner         = var.owner
    project       = var.project
    "cost-center" = var.cost_center
    team          = var.team
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(local.required_tags, {
      environment = var.environment
      managed-by  = "terraform"
    })
  }

  # Tables created before these tags existed carry a CreatedBy tag. The organisation's service control policy denies
  # dynamodb:UntagResource, so Terraform must leave that tag alone instead of trying to remove it.
  ignore_tags {
    keys = ["CreatedBy"]
  }
}

# Needed once, to destroy the old CloudFront web ACL and IP sets that are still in state.
# After `./deploy.sh <env> up` has removed them, delete this block (and the mock in tests/plan.tftest.hcl).
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = merge(local.required_tags, {
      environment = var.environment
      managed-by  = "terraform"
    })
  }
}
