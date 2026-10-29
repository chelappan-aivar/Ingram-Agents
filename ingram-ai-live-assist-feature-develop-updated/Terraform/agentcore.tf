# Bedrock AgentCore: two memories and the runtime that hosts the agent.
# A runtime session is one call. The same session follows a live meeting (see src/app/meeting_join.py).

resource "aws_bedrockagentcore_memory" "conversation" {
  name                  = local.memory_name
  description           = "Live Assist conversation history (${local.name})"
  event_expiry_duration = var.data_retention_days
}

# Facts per account and opportunity, kept across calls.
resource "aws_bedrockagentcore_memory" "long_term" {
  name                  = local.ltm_name
  description           = "Live Assist long-term memory across calls (${local.name})"
  event_expiry_duration = var.data_retention_days
}

resource "aws_bedrockagentcore_memory_strategy" "call_facts" {
  memory_id           = aws_bedrockagentcore_memory.long_term.id
  name                = "CallFacts"
  description         = "Facts about the customer and the opportunity from curated call notes"
  type                = "SEMANTIC"
  namespace_templates = ["/facts/{actorId}/"] # Must match app.memory.facts_namespace.
}

resource "aws_bedrockagentcore_agent_runtime" "agent" {
  count = local.runtime_enabled ? 1 : 0

  agent_runtime_name = local.runtime_name
  description        = "Live Assist agent (${local.name})"
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.agent.repository_url}:${var.agent_image_tag}"
    }
  }

  # PUBLIC: the runtime must reach the Vexa meeting API and Bedrock over the internet.
  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  lifecycle_configuration = [{
    idle_runtime_session_timeout = var.runtime_idle_timeout_seconds
    max_lifetime                 = var.runtime_max_lifetime_seconds
  }]

  # No secrets here. The Vexa keys arrive in a meeting.join event and stay in the session's memory.
  environment_variables = {
    AGENTCORE_MEMORY_ID      = aws_bedrockagentcore_memory.conversation.id
    LONG_TERM_MEMORY_ID      = aws_bedrockagentcore_memory.long_term.id
    BEDROCK_MODEL_ID         = var.model_id
    SESSION_TABLE_NAME       = aws_dynamodb_table.sessions.name
    TIMELINE_TABLE_NAME      = aws_dynamodb_table.timeline.name
    PROCESS_DEBOUNCE_SECONDS = var.process_debounce_seconds
    DATA_RETENTION_DAYS      = tostring(var.data_retention_days)
    VEXA_API_BASE            = var.vexa_api_base
    LOG_LEVEL                = "INFO"
  }

  depends_on = [aws_iam_role_policy.runtime]
}
