# The console's shared access key. This is NOT a Vexa key: those are typed in the console per call and
# never stored anywhere in AWS. The value is generated here and read by the API Lambda. Users enter it in the
# console once per browser session (it is kept in sessionStorage only), so treat it like a shared password.

resource "random_password" "access_key" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "access_key" {
  #checkov:skip=CKV2_AWS_57:A shared key checked by the API; automatic rotation would lock users out. Rotate with `terraform apply -replace=random_password.access_key`.
  name                    = "${local.name}/access-key"
  description             = "Shared access key for the Live Assist console and API"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = var.environment == "prod" ? 30 : 0
}

resource "aws_secretsmanager_secret_version" "access_key" {
  secret_id     = aws_secretsmanager_secret.access_key.id
  secret_string = random_password.access_key.result
}

# --- Recall.ai (used when meeting_provider = "recall") ----------------------------------------------------
# Terraform creates the empty secrets only. Put the values in yourself so they never reach Terraform state:
#   aws secretsmanager put-secret-value --secret-id <recall_api_key_secret_name>      --secret-string '<Recall API key>'
#   aws secretsmanager put-secret-value --secret-id <recall_verify_secret_name> --secret-string 'whsec_...'
# The second is the workspace verification secret (Recall dashboard: Developers, API Keys & Secrets).

resource "aws_secretsmanager_secret" "recall_api_key" {
  #checkov:skip=CKV2_AWS_57:A third-party API key; Recall has no rotation hook. Rotate by creating a new key in Recall and putting the value here.
  name                    = "${local.name}/recall-api-key"
  description             = "Recall.ai API key used by the Live Assist API to send meeting bots"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = var.environment == "prod" ? 30 : 0
}

resource "aws_secretsmanager_secret" "recall_verify" {
  #checkov:skip=CKV2_AWS_57:Recall rotates this with its own 24 hour overlap; put the new value here after rotating in the Recall dashboard.
  name                    = "${local.name}/recall-verify-secret"
  description             = "Recall.ai workspace verification secret; the API checks every webhook's signature with it"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = var.environment == "prod" ? 30 : 0
}
