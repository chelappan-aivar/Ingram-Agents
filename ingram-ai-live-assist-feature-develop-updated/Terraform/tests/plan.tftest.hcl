# Offline plan checks (no AWS access): terraform test
# The provider is mocked, so these verify the configuration's logic, not the real AWS behaviour.

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_canonical_user_id" {
    defaults = { id = "canonicaluserid" }
  }
  mock_data "aws_cloudfront_log_delivery_canonical_user_id" {
    defaults = { id = "logdeliveryuserid" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

mock_provider "aws" {
  alias = "us_east_1"
}

mock_provider "random" {}

variables {
  environment = "dev"
  owner       = "owner"
  team        = "team"
  cost_center = "cc"
}

run "no_agent_until_an_image_is_pushed" {
  command = plan

  assert {
    condition     = length(aws_bedrockagentcore_agent_runtime.agent) == 0
    error_message = "The runtime must not exist while agent_image_tag is empty."
  }
}

run "agent_runs_the_pushed_image" {
  command = plan

  variables {
    agent_image_tag = "abc1234-20261022"
  }

  assert {
    condition     = length(aws_bedrockagentcore_agent_runtime.agent) == 1
    error_message = "A tag means the runtime is deployed."
  }

  assert {
    condition     = aws_iam_role.runtime.name == "ila-dev-BedrockAgentCoreRuntime"
    error_message = "AgentCore only accepts roles with BedrockAgentCore in the name."
  }
}

run "no_vexa_key_is_configurable" {
  command = plan

  variables {
    agent_image_tag = "abc1234-20261022"
  }

  assert {
    condition = alltrue([
      for k, v in aws_bedrockagentcore_agent_runtime.agent[0].environment_variables : !can(regex("(?i)key|token|secret", k))
    ])
    error_message = "The runtime environment must hold no credentials: Vexa keys come from the console."
  }
}

run "vexa_is_the_default_meeting_provider" {
  command = plan

  assert {
    condition     = aws_lambda_function.api.environment[0].variables["MEETING_PROVIDER"] == "vexa"
    error_message = "Without meeting_provider the API keeps using Vexa."
  }
}

run "recall_provider_is_selected_by_one_variable" {
  command = plan

  variables {
    meeting_provider = "recall"
  }

  assert {
    condition     = aws_lambda_function.api.environment[0].variables["MEETING_PROVIDER"] == "recall"
    error_message = "meeting_provider = recall must reach the API function."
  }

  assert {
    condition     = length([for k in keys(aws_lambda_function.api.environment[0].variables) : k if k == "RECALL_API_KEY" || k == "RECALL_VERIFY_SECRET"]) == 0
    error_message = "The function environment names the Recall secrets; it must never hold their values."
  }
}

run "unknown_meeting_provider_is_refused" {
  command = plan

  variables {
    meeting_provider = "zoom-bot"
  }

  expect_failures = [var.meeting_provider]
}

run "audio_source_is_optional" {
  command = plan

  variables {
    enable_audio_source = false
  }

  assert {
    condition     = length(aws_s3_bucket.uploads) == 0
    error_message = "No upload bucket without the audio source."
  }
}

run "console_is_hosted_on_amplify" {
  command = plan

  assert {
    condition     = aws_amplify_branch.console.branch_name == "main"
    error_message = "The console is deployed to the main Amplify branch."
  }
}
