# Roles for the two Lambda functions. Same permissions as src/deploy/templates/api-policy.json and
# replayer-policy.json, plus the customer-managed key.

locals {
  api_function_name      = "${local.name}-api"
  replayer_function_name = "${local.name}-replayer"
  uploads_arn            = var.enable_audio_source ? aws_s3_bucket.uploads[0].arn : ""
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# --- API ---------------------------------------------------------------------------

resource "aws_iam_role" "api" {
  name               = "${local.name}-api"
  description        = "Live Assist API function (${local.name})"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

# The wildcards are an index list and the uploads/ key prefix: both are the narrowest scope that works.
#tfsec:ignore:aws-iam-no-policy-wildcards
data "aws_iam_policy_document" "api" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }

  statement {
    sid     = "SendEvents"
    actions = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [
      local.runtime_arn_pattern,
      "${local.runtime_arn_pattern}/runtime-endpoint/*",
    ]
  }

  statement {
    sid     = "ReadCalls"
    actions = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [
      aws_dynamodb_table.sessions.arn,
      aws_dynamodb_table.timeline.arn,
      "${aws_dynamodb_table.timeline.arn}/index/*",
    ]
  }

  statement {
    sid       = "WriteAccountsAndConfigs"
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.timeline.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["account#*", "config#*", "intake#*"] # intake#: call history map per Intake Agent opportunity
    }
  }

  statement {
    sid       = "ReadIntakeCallMap"
    actions   = ["dynamodb:BatchGetItem"]
    resources = [aws_dynamodb_table.timeline.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["intake#*"]
    }
  }

  dynamic "statement" {
    for_each = var.intake_table_name == "" ? [] : [1]
    content {
      sid     = "ReadIntakeFundingChecks"
      actions = ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query"]
      resources = [
        "arn:${local.partition}:dynamodb:${local.region}:${local.account_id}:table/${var.intake_table_name}",
        "arn:${local.partition}:dynamodb:${local.region}:${local.account_id}:table/${var.intake_table_name}/index/owner-created",
      ]
    }
  }

  statement {
    sid       = "StartSources"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:${local.partition}:lambda:${local.region}:${local.account_id}:function:${local.replayer_function_name}"]
  }

  statement {
    sid       = "AccessKey"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.access_key.arn]
  }

  statement {
    sid       = "LongTermMemory"
    actions   = ["bedrock-agentcore:ListMemoryRecords", "bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:BatchDeleteMemoryRecords"]
    resources = [local.ltm_arn_pattern]
  }

  # Read-only, and only while Recall is the meeting provider.
  dynamic "statement" {
    for_each = var.meeting_provider == "recall" ? [1] : []

    content {
      sid       = "RecallSecrets"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = [aws_secretsmanager_secret.recall_api_key.arn, aws_secretsmanager_secret.recall_verify.arn]
    }
  }

  statement {
    sid       = "CallDataKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [aws_kms_key.main.arn]
  }

  dynamic "statement" {
    for_each = var.enable_audio_source ? [1] : []

    content {
      sid       = "Uploads"
      actions   = ["s3:PutObject", "s3:GetObject"]
      resources = ["${local.uploads_arn}/uploads/*"]
    }
  }

  dynamic "statement" {
    for_each = var.enable_audio_source ? [1] : []

    content {
      sid       = "UploadsMissingIs404"
      actions   = ["s3:ListBucket"]
      resources = [local.uploads_arn]

      condition {
        test     = "StringLike"
        variable = "s3:prefix"
        values   = ["uploads/*"]
      }
    }
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "access"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# --- replayer (plays recordings and pasted transcripts into a call; streams audio through Transcribe) ---

resource "aws_iam_role" "replayer" {
  name               = "${local.name}-replayer"
  description        = "Live Assist replayer function (${local.name})"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

#tfsec:ignore:aws-iam-no-policy-wildcards
data "aws_iam_policy_document" "replayer" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.replayer.arn}:*"]
  }

  statement {
    sid     = "SendEvents"
    actions = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [
      local.runtime_arn_pattern,
      "${local.runtime_arn_pattern}/runtime-endpoint/*",
    ]
  }

  statement {
    sid       = "ReadCallStatus"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.timeline.arn]
  }

  statement {
    sid       = "ContinueLongSources"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:${local.partition}:lambda:${local.region}:${local.account_id}:function:${local.replayer_function_name}"]
  }

  statement {
    sid       = "CallDataKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [aws_kms_key.main.arn]
  }

  dynamic "statement" {
    for_each = var.enable_audio_source ? [1] : []

    content {
      sid       = "ReadUploads"
      actions   = ["s3:GetObject"]
      resources = ["${local.uploads_arn}/uploads/*"]
    }
  }

  dynamic "statement" {
    for_each = var.enable_audio_source ? [1] : []

    content {
      sid       = "UploadsMissingIs404"
      actions   = ["s3:ListBucket"]
      resources = [local.uploads_arn]

      condition {
        test     = "StringLike"
        variable = "s3:prefix"
        values   = ["uploads/*"]
      }
    }
  }

  dynamic "statement" {
    for_each = var.enable_audio_source ? [1] : []

    content {
      sid       = "Transcribe"
      actions   = ["transcribe:StartStreamTranscription", "transcribe:StartStreamTranscriptionWebSocket"]
      resources = ["*"] # Streaming transcription has no resource-level permissions.
    }
  }
}

resource "aws_iam_role_policy" "replayer" {
  name   = "access"
  role   = aws_iam_role.replayer.id
  policy = data.aws_iam_policy_document.replayer.json
}
