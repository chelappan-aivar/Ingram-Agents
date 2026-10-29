# Live Assist, prod. Edit every CHANGE_ME before the first deploy: deploy.sh refuses to run until they are gone.

environment = "prod"
aws_region  = "us-east-1"

# Tags required by the organisation's policy check (configuration.json: required_tags).
owner       = "CHANGE_ME"
team        = "CHANGE_ME"
cost_center = "CHANGE_ME"

# Audio-file source: upload bucket and Amazon Transcribe. The meeting-link and transcript sources do not need it.
enable_audio_source = true

# Image the runtime runs. deploy.sh image builds, pushes and writes this line. Empty: agent not deployed yet.
agent_image_tag = ""

model_id            = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
data_retention_days = 180
