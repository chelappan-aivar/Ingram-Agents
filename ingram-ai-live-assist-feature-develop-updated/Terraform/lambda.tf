# Two functions built from one zip (src/deploy/build_lambda.py):
#   api       the HTTP API behind API Gateway
#   replayer  plays a recording or pasted transcript into a call, and streams audio through Transcribe
# Live meetings: with meeting_provider = "vexa" they run inside the AgentCore Runtime session that owns the call.
# With "recall" the api function sends the bot and receives Recall's signed webhooks (src/api/recall.py).

locals {
  # A CI plan runs without the zip. deploy.sh builds it before every plan and apply.
  package_hash = try(filebase64sha256(var.lambda_package_path), null)

  runtime_arn = local.runtime_enabled ? aws_bedrockagentcore_agent_runtime.agent[0].agent_runtime_arn : ""
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.api_function_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
}

resource "aws_cloudwatch_log_group" "replayer" {
  name              = "/aws/lambda/${local.replayer_function_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
}

resource "aws_lambda_function" "replayer" {
  #checkov:skip=CKV_AWS_117:No VPC resources are used; it calls AWS APIs only.
  #checkov:skip=CKV_AWS_116:Async retries are off (a retried source would send its turns twice); a failed source ends its call.
  #checkov:skip=CKV_AWS_115:Reserved concurrency needs spare account quota that new accounts lack. API Gateway throttling bounds the load.
  #checkov:skip=CKV_AWS_272:Code signing is not set up for this project.
  function_name    = local.replayer_function_name
  description      = "Live Assist replayer (${local.name})"
  role             = aws_iam_role.replayer.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "replayer.lambda_handler.handler"
  timeout          = 900
  memory_size      = 512
  filename         = var.lambda_package_path
  source_code_hash = local.package_hash
  kms_key_arn      = aws_kms_key.main.arn

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      TIMELINE_TABLE_NAME   = aws_dynamodb_table.timeline.name
      AGENTCORE_RUNTIME_ARN = local.runtime_arn
      AGENTCORE_QUALIFIER   = "DEFAULT"
      LOG_LEVEL             = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.replayer, aws_iam_role_policy.replayer]
}

# A retried source would send its turns twice, so asynchronous invocations never retry.
resource "aws_lambda_function_event_invoke_config" "replayer" {
  function_name                = aws_lambda_function.replayer.function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 3600
}

resource "aws_lambda_function" "api" {
  #checkov:skip=CKV_AWS_117:No VPC resources are used; it calls AWS APIs only.
  #checkov:skip=CKV_AWS_116:Synchronous API handler behind API Gateway; callers see errors directly.
  #checkov:skip=CKV_AWS_115:Reserved concurrency needs spare account quota that new accounts lack. API Gateway throttling bounds the load.
  #checkov:skip=CKV_AWS_272:Code signing is not set up for this project.
  function_name    = local.api_function_name
  description      = "Live Assist API (${local.name})"
  role             = aws_iam_role.api.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "api.handler.handler"
  timeout          = 30
  memory_size      = 512
  filename         = var.lambda_package_path
  source_code_hash = local.package_hash
  kms_key_arn      = aws_kms_key.main.arn

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      TIMELINE_TABLE_NAME   = aws_dynamodb_table.timeline.name
      SESSION_TABLE_NAME    = aws_dynamodb_table.sessions.name
      ACCESS_KEY_SECRET     = aws_secretsmanager_secret.access_key.name
      AGENTCORE_RUNTIME_ARN = local.runtime_arn
      AGENTCORE_QUALIFIER   = "DEFAULT"
      REPLAYER_FUNCTION     = aws_lambda_function.replayer.function_name
      LONG_TERM_MEMORY_ID   = aws_bedrockagentcore_memory.long_term.id
      DATA_RETENTION_DAYS   = tostring(var.data_retention_days)
      UPLOAD_BUCKET         = var.enable_audio_source ? aws_s3_bucket.uploads[0].bucket : ""
      DIRECT_UPLOAD         = "true" # The hosted console has no upload proxy: browsers PUT to S3 themselves.
      LOG_LEVEL             = "INFO"
      INTAKE_TABLE_NAME     = var.intake_table_name # Intake Agent funding checks, read-only. Empty: card hidden.
      DISCOVERY_API_URL     = var.discovery_api_url # Discovery agent API (open): run status and report. Empty: off.
      DISCOVERY_UI_URL      = var.discovery_ui_url
      CRM_BASE_URL          = var.crm_base_url      # Dynamics origin for the CRM link on the Discovery page. Empty: none.

      # Meeting source. With "recall" this function sends the bot and receives Recall's signed webhooks at
      # <PUBLIC_API_URL>/recall/transcript. Secret names only: the values stay in Secrets Manager.
      MEETING_PROVIDER          = var.meeting_provider
      RECALL_REGION             = var.recall_region
      PUBLIC_API_URL            = aws_apigatewayv2_api.api.api_endpoint
      RECALL_API_KEY_SECRET     = aws_secretsmanager_secret.recall_api_key.name
      RECALL_VERIFY_SECRET_NAME = aws_secretsmanager_secret.recall_verify.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api]
}
