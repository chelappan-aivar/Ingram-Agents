# Live Assist, dev. Set owner, team and cost_center before deploying.

environment = "dev"
aws_region  = "us-east-1"
# Prefix for every resource name. "ila" is already used by an older deployment in this account.
project = "lassist"

# Tags required by the organisation's policy check (configuration.json: required_tags).
owner       = "chelappan.kumar@aivar.tech"
team        = "ai-ml"
cost_center = "ai-ml"

# Audio-file source: upload bucket and Amazon Transcribe. The meeting-link and transcript sources do not need it.
enable_audio_source = true

# Image the runtime runs. deploy.sh image builds, pushes and writes this line. Empty: agent not deployed yet.
agent_image_tag = "build-20261027125855"

# Intake Agent funding checks shown in the console (read-only).
intake_table_name = "ingram-intake-runs"

# Discovery agent: its runs for an Intake opportunity are shown in the console; it reads the calls back (two-way).
discovery_api_url = "https://eaxi52af2c.execute-api.us-east-1.amazonaws.com"
discovery_ui_url  = "https://main.d2n5894mve8es1.amplifyapp.com"

model_id            = "global.openai.gpt-5.6-luna"
data_retention_days = 180

# Meeting bot provider. recall: the API sends a Recall.ai bot (put the Recall key and verification secret in the two
# Secrets Manager secrets first, see Terraform/README.md). vexa: roll back by setting this to "vexa".
meeting_provider = "recall"
recall_region    = "us-east-1"
