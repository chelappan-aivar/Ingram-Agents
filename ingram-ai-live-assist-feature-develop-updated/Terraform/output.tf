output "console_url" {
  description = "The console on Amplify. Users enter the access key (access_key_secret_name) when asked."
  value       = local.console_origin
}

output "api_endpoint_direct" {
  description = "API Gateway address the console calls. Every request needs the access key."
  value       = aws_apigatewayv2_api.api.api_endpoint
}

output "amplify_app_id" {
  value = aws_amplify_app.console.id
}

output "amplify_branch" {
  value = aws_amplify_branch.console.branch_name
}

output "region" {
  value = var.aws_region
}

output "agent_image_tag" {
  description = "Image tag the runtime runs. Empty: the agent is not deployed yet."
  value       = var.agent_image_tag
}

output "ecr_repository_url" {
  value = aws_ecr_repository.agent.repository_url
}

output "agent_runtime_arn" {
  value = local.runtime_arn
}

output "conversation_memory_id" {
  value = aws_bedrockagentcore_memory.conversation.id
}

output "long_term_memory_id" {
  description = "deploy.sh retention sets the 180-day expiry on this memory's facts; Terraform cannot."
  value       = aws_bedrockagentcore_memory.long_term.id
}

output "long_term_memory_name" {
  value = local.ltm_name
}

output "uploads_bucket" {
  value = var.enable_audio_source ? aws_s3_bucket.uploads[0].bucket : ""
}

output "api_function_name" {
  value = aws_lambda_function.api.function_name
}

output "replayer_function_name" {
  value = aws_lambda_function.replayer.function_name
}

output "access_key_secret_name" {
  description = "Secrets Manager secret holding the console access key. Give its value to the people who may use the console."
  value       = aws_secretsmanager_secret.access_key.name
}

output "meeting_provider" {
  description = "Who sends the meeting bot: recall or vexa."
  value       = var.meeting_provider
}

output "recall_api_key_secret_name" {
  description = "Put the Recall API key here: aws secretsmanager put-secret-value --secret-id <name> --secret-string '<key>'"
  value       = aws_secretsmanager_secret.recall_api_key.name
}

output "recall_verify_secret_name" {
  description = "Put the Recall workspace verification secret (whsec_...) here."
  value       = aws_secretsmanager_secret.recall_verify.name
}

output "recall_status_webhook_url" {
  description = "Add this URL once in the Recall dashboard (Webhooks) and subscribe it to bot.status_change, so a call ends when its meeting ends. The transcript webhook is set per bot by the API."
  value       = "${aws_apigatewayv2_api.api.api_endpoint}/recall/status"
}

output "kms_key_arn" {
  value = aws_kms_key.main.arn
}

output "data_retention_days" {
  value = var.data_retention_days
}
