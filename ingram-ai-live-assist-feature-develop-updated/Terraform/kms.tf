# One customer-managed key for call data at rest: DynamoDB, uploads, the access-key secret,
# Lambda environment variables, ECR images and CloudWatch logs.

data "aws_iam_policy_document" "kms" {
  #checkov:skip=CKV_AWS_111:A key policy applies to its own key, so "*" means this key; the account-root statement is how IAM policies are allowed to grant access.
  #checkov:skip=CKV_AWS_356:Same: in a key policy "*" is the key itself.
  #checkov:skip=CKV_AWS_109:Same: the account-root statement is the standard key policy.
  statement {
    sid       = "AccountAdministration"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:root"]
    }
  }

  statement {
    sid       = "CloudWatchLogs"
    actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["logs.${var.aws_region}.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${local.account_id}:log-group:*"]
    }
  }
}

resource "aws_kms_key" "main" {
  description             = "${local.name} call data"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.kms.json
}

resource "aws_kms_alias" "main" {
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.main.key_id
}
