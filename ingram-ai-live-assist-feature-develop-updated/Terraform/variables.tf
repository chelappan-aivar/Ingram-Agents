# --- identity and tags -------------------------------------------------------------

variable "project" {
  description = "Short name that starts every resource name."
  type        = string
  default     = "ila"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,9}$", var.project))
    error_message = "project must be 2-10 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Environment name. Resources are named <project>-<environment>-..."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "aws_region" {
  description = "Region for the deployment. Must offer Bedrock AgentCore and AWS Amplify."
  type        = string
  default     = "us-east-1"
}

variable "owner" {
  description = "Tag: person or group accountable for these resources."
  type        = string
}

variable "team" {
  description = "Tag: owning team."
  type        = string
}

variable "cost_center" {
  description = "Tag: cost center that pays for these resources."
  type        = string
}

# --- agent ---------------------------------------------------------------------------

variable "agent_image_tag" {
  description = "Tag of the agent image in ECR. Empty means the agent is not deployed yet: everything else is still created. deploy.sh image sets it."
  type        = string
  default     = ""
}

variable "model_id" {
  description = "Bedrock inference profile id. Model access must be enabled in the account."
  type        = string
  default     = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "vexa_api_base" {
  description = "Vexa meeting API address. The Vexa keys are NOT configured here: users type them in the console."
  type        = string
  default     = "https://api.cloud.vexa.ai"

  validation {
    condition     = startswith(var.vexa_api_base, "https://")
    error_message = "vexa_api_base must be an https address."
  }
}

variable "meeting_provider" {
  description = "Who sends the bot into a meeting and delivers its transcript: recall (the API sends a Recall.ai bot and receives signed webhooks) or vexa (the runtime follows the meeting with a Vexa bot; keys are typed in the console). Switch back to vexa to roll back."
  type        = string
  default     = "vexa"

  validation {
    condition     = contains(["vexa", "recall"], var.meeting_provider)
    error_message = "meeting_provider must be vexa or recall."
  }
}

variable "recall_region" {
  description = "Recall.ai region of the workspace: us-east-1, us-west-2 (pay as you go), eu-central-1 or ap-northeast-1. Used only when meeting_provider is recall. The Recall keys are NOT configured here: put them in the two Secrets Manager secrets (see Terraform/README.md)."
  type        = string
  default     = "us-west-2"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.recall_region))
    error_message = "recall_region must look like us-west-2."
  }
}

variable "runtime_idle_timeout_seconds" {
  description = "A runtime session that reports itself idle ends after this long. A session following a meeting reports busy."
  type        = number
  default     = 900
}

variable "runtime_max_lifetime_seconds" {
  description = "Longest a single runtime session (one call) can live. Must cover the longest meeting."
  type        = number
  default     = 28800
}

variable "process_debounce_seconds" {
  description = "Seconds the agent waits after a turn to group bursts into one run."
  type        = string
  default     = "1.0"
}

variable "enable_audio_source" {
  description = "Create the upload bucket and the Transcribe permissions for the audio-file source."
  type        = bool
  default     = true
}

# --- data and operations -----------------------------------------------------------------

variable "data_retention_days" {
  description = "How long call data is kept (DynamoDB TTL, memory events, uploads)."
  type        = number
  default     = 180

  validation {
    condition     = var.data_retention_days >= 31 && var.data_retention_days <= 365
    error_message = "data_retention_days must be 31-365 (31 is the shortest AgentCore fact expiry)."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention. 365 is the shortest value the organisation's Checkov policy accepts."
  type        = number
  default     = 365
}

variable "api_throttle_rate" {
  description = "API Gateway steady-state requests per second."
  type        = number
  default     = 20
}

variable "api_throttle_burst" {
  description = "API Gateway burst."
  type        = number
  default     = 40
}

variable "lambda_package_path" {
  description = "Zip made by deploy.sh (src/deploy/build_lambda.py). Read only when it exists, so a CI plan works without it."
  type        = string
  default     = "build/functions.zip"
}

variable "intake_table_name" {
  description = "DynamoDB table of the Intake Agent (its FUNDLATEST# items are listed in the console, read-only). Empty turns the feature off."
  type        = string
  default     = ""

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]{0,255}$", var.intake_table_name))
    error_message = "intake_table_name must be a DynamoDB table name or empty."
  }
}

variable "discovery_api_url" {
  description = "Discovery agent HTTP API. Live Assist reads its runs' status and report for the Intake opportunities. Empty turns it off."
  type        = string
  default     = ""

  validation {
    condition     = var.discovery_api_url == "" || can(regex("^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._/-]*)?$", var.discovery_api_url))
    error_message = "discovery_api_url must be an https URL or empty."
  }
}

variable "crm_base_url" {
  description = "Dynamics 365 origin (for example https://yourorg.crm.dynamics.com). Builds the CRM link shown on the Discovery page. Empty: no link."
  type        = string
  default     = ""
  validation {
    condition     = var.crm_base_url == "" || can(regex("^https://[A-Za-z0-9.-]+$", var.crm_base_url))
    error_message = "crm_base_url must be an https origin or empty."
  }
}
variable "discovery_ui_url" {
  description = "Discovery agent web UI, linked from the console (open a run, start one for an opportunity)."
  type        = string
  default     = ""

  validation {
    condition     = var.discovery_ui_url == "" || can(regex("^https://[A-Za-z0-9.-]+$", var.discovery_ui_url))
    error_message = "discovery_ui_url must be an https origin or empty."
  }
}
