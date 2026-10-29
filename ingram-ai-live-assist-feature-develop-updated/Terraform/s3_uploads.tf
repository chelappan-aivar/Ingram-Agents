# Audio uploads for the audio-file source: the browser converts a recording to 16 kHz PCM and PUTs it here with
# a presigned URL; the replayer Lambda streams it through Amazon Transcribe. Optional: enable_audio_source.

resource "aws_s3_bucket" "uploads" {
  count = var.enable_audio_source ? 1 : 0

  #checkov:skip=CKV_AWS_144:Call audio is not replicated to a second region.
  #checkov:skip=CKV2_AWS_62:Nothing reacts to upload events; the API starts the source explicitly.
  bucket        = "${local.name}-uploads-${local.account_id}-${var.aws_region}"
  force_destroy = var.environment != "prod"
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  count = var.enable_audio_source ? 1 : 0

  bucket                  = aws_s3_bucket.uploads[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  count = var.enable_audio_source ? 1 : 0

  bucket = aws_s3_bucket.uploads[0].id

  rule {
    bucket_key_enabled = true

    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
  }
}

resource "aws_s3_bucket_versioning" "uploads" {
  count = var.enable_audio_source ? 1 : 0

  bucket = aws_s3_bucket.uploads[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_logging" "uploads" {
  count = var.enable_audio_source ? 1 : 0

  bucket        = aws_s3_bucket.uploads[0].id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "s3/uploads/"
  depends_on    = [aws_s3_bucket_policy.logs]
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  count = var.enable_audio_source ? 1 : 0

  bucket     = aws_s3_bucket.uploads[0].id
  depends_on = [aws_s3_bucket_versioning.uploads]

  rule {
    id     = "expire-uploads"
    status = "Enabled"

    filter {
      prefix = "uploads/"
    }

    expiration {
      days = var.data_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  rule {
    id     = "abort-unfinished-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# The console page is the only origin that may PUT. The page and the bucket are different origins.
resource "aws_s3_bucket_cors_configuration" "uploads" {
  count = var.enable_audio_source ? 1 : 0

  bucket = aws_s3_bucket.uploads[0].id

  cors_rule {
    allowed_origins = [local.console_origin]
    allowed_methods = ["PUT"]
    allowed_headers = ["*"]
    max_age_seconds = 600
  }
}

data "aws_iam_policy_document" "uploads_bucket" {
  count = var.enable_audio_source ? 1 : 0

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.uploads[0].arn, "${aws_s3_bucket.uploads[0].arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "uploads" {
  count = var.enable_audio_source ? 1 : 0

  bucket     = aws_s3_bucket.uploads[0].id
  policy     = data.aws_iam_policy_document.uploads_bucket[0].json
  depends_on = [aws_s3_bucket_public_access_block.uploads]
}
