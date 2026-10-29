# The console (src/console: static HTML, CSS and ES modules, no build step) is hosted on AWS Amplify.
# The browser calls API Gateway directly (CORS allows only this Amplify origin) and sends the shared
# access key, which the user types once per browser session. deploy.sh uploads the files (manual deploy).

locals {
  # Browsers PUT audio straight to the uploads bucket. us-east-1 presigned URLs use the global host name.
  upload_origins = var.enable_audio_source ? [
    "https://${aws_s3_bucket.uploads[0].bucket}.s3.amazonaws.com",
    "https://${aws_s3_bucket.uploads[0].bucket}.s3.${var.aws_region}.amazonaws.com",
  ] : []

  # The API host is matched by wildcard so this does not depend on the API (its CORS depends on this app).
  content_security_policy = join("; ", [
    "default-src 'self'",
    "img-src 'self' data:",
    "connect-src ${join(" ", concat(["'self'", "https://*.execute-api.${var.aws_region}.amazonaws.com"], local.upload_origins))}",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
  ])

  console_branch = "main"
  console_origin = "https://${local.console_branch}.${aws_amplify_app.console.default_domain}"
}

resource "aws_amplify_app" "console" {
  name     = "${local.name}-console"
  platform = "WEB"

  custom_headers = yamlencode({
    customHeaders = [{
      pattern = "**"
      headers = [
        { key = "Content-Security-Policy", value = local.content_security_policy },
        { key = "X-Content-Type-Options", value = "nosniff" },
        { key = "X-Frame-Options", value = "DENY" },
        { key = "Referrer-Policy", value = "no-referrer" },
        { key = "Strict-Transport-Security", value = "max-age=63072000; includeSubDomains; preload" },
        { key = "Permissions-Policy", value = "camera=(), geolocation=(), microphone=(), payment=()" },
        { key = "Cache-Control", value = "no-store" },
      ]
    }]
  })
}

resource "aws_amplify_branch" "console" {
  app_id            = aws_amplify_app.console.id
  branch_name       = local.console_branch
  stage             = var.environment == "prod" ? "PRODUCTION" : "DEVELOPMENT"
  enable_auto_build = false
}
