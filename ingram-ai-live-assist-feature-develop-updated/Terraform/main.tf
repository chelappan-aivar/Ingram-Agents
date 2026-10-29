data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  name       = "${var.project}-${var.environment}"
  name_us    = replace(local.name, "-", "_") # AgentCore names allow letters, digits and "_".

  # AgentCore only accepts roles whose name contains "BedrockAgentCore".
  runtime_role_name = "${local.name}-BedrockAgentCoreRuntime"
  runtime_name      = "${local.name_us}_runtime"
  memory_name       = "${local.name_us}_memory"
  ltm_name          = "${local.name_us}_ltm"

  runtime_enabled = var.agent_image_tag != ""

  # "global.anthropic.x" -> "anthropic.x": the foundation model behind an inference profile.
  foundation_model_id = can(regex("^(global|us|eu|apac|jp|au|ca|us-gov)\\.", var.model_id)) ? join(".", slice(split(".", var.model_id), 1, length(split(".", var.model_id)))) : var.model_id

  # Used to build ARNs that must not depend on the resources they are granted on.
  runtime_arn_pattern = "arn:${data.aws_partition.current.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:runtime/${local.runtime_name}-*"
  ltm_arn_pattern     = "arn:${data.aws_partition.current.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:memory/${local.ltm_name}-*"
  memory_arn_pattern  = "arn:${data.aws_partition.current.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:memory/${local.memory_name}-*"
}
