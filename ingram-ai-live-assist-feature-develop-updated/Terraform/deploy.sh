#!/usr/bin/env bash
# Live Assist on AWS: deploy, update or remove one environment with Terraform.
#
#   ./deploy.sh <env> check       offline: format, validate, tests, and the scanners that are installed
#   ./deploy.sh <env> plan        read-only: show what `up` would change
#   ./deploy.sh <env> up          first deploy or full update: infrastructure, agent image, retention
#   ./deploy.sh <env> image       build and push the agent image, then point the runtime at it
#   ./deploy.sh <env> update      rebuild the Lambda package, re-apply and redeploy the console
#   ./deploy.sh <env> console     upload src/console to Amplify only (console changes, no infrastructure change)
#   ./deploy.sh <env> retention   set the long-term memory expiry (Terraform cannot)
#   ./deploy.sh <env> outputs     print the console address and resource names
#   ./deploy.sh <env> down        destroy everything (asks you to type the environment name)
#
# <env> is dev or prod: it picks environments/<env>.tfvars. Needs terraform 1.7+, the AWS CLI, jq and python3;
# `up` and `image` also need Docker able to build linux/arm64. It uses the AWS credentials in your environment.
# Every apply shows its plan and asks for approval. The Vexa keys are never handled here: users type them in
# the console.
set -euo pipefail

TF_DIR=$(cd "$(dirname "$0")" && pwd)
SRC_DIR=$(cd "$TF_DIR/../src" && pwd)
cd "$TF_DIR"

log()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    \033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

[ $# -eq 2 ] || usage
ENV_NAME=$1
COMMAND=$2
VAR_FILE="environments/$ENV_NAME.tfvars"
[ -f "$VAR_FILE" ] || die "missing $TF_DIR/$VAR_FILE (environments: $(ls environments | sed 's/\.tfvars//' | tr '\n' ' '))"

PYTHON=${PYTHON:-}
if [ -z "$PYTHON" ]; then
  if [ -x "$SRC_DIR/.venv/bin/python" ]; then PYTHON="$SRC_DIR/.venv/bin/python"; else PYTHON=python3; fi
fi
ZIP="build/functions.zip"
export AWS_PAGER=""

# --- tfvars helpers ------------------------------------------------------------------

tfvar() { # tfvar <name> <default>: the string value in the var file, or the default
  local value
  value=$(sed -n "s/^$1[[:space:]]*=[[:space:]]*\"\\(.*\\)\"[[:space:]]*\\(#.*\\)\\{0,1\\}\$/\\1/p" "$VAR_FILE" | head -n 1)
  if [ -n "$value" ] || grep -Eq "^$1[[:space:]]*=" "$VAR_FILE"; then printf '%s' "$value"; else printf '%s' "$2"; fi
}

set_tfvar() { # set_tfvar <name> <value>: change or add a string line, keeping the rest of the file
  local tmp
  tmp=$(mktemp)
  awk -v k="$1" -v v="$2" '
    $0 ~ "^" k "[[:space:]]*=" { print k " = \"" v "\""; done = 1; next }
    { print }
    END { if (!done) print k " = \"" v "\"" }' "$VAR_FILE" > "$tmp"
  cat "$tmp" > "$VAR_FILE"
  rm -f "$tmp"
}

REGION=$(tfvar aws_region us-east-1)
PROJECT=$(tfvar project ila)
NAME="$PROJECT-$ENV_NAME"
NAME_US=$(printf '%s' "$NAME" | tr '-' '_')
RETENTION_DAYS=$(sed -n 's/^data_retention_days[[:space:]]*=[[:space:]]*\([0-9]*\).*/\1/p' "$VAR_FILE" | head -n 1)
RETENTION_DAYS=${RETENTION_DAYS:-180}
MODEL_ID=$(tfvar model_id "$(sed -n '/variable "model_id"/,/^}/s/.*default *= *"\(.*\)".*/\1/p' variables.tf | head -n 1)")
export AWS_REGION=$REGION AWS_DEFAULT_REGION=$REGION

# --- checks ----------------------------------------------------------------------------

need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required${2:+ ($2)}"; }

tf_version_ok() { # terraform >= $1 (major.minor)
  local have
  have=$(terraform version -json | jq -r .terraform_version)
  [ "$(printf '%s\n%s\n' "$1" "$have" | sort -V | head -n 1)" = "$1" ]
}

preflight() {
  need terraform; need jq; need aws
  tf_version_ok 1.7 || die "terraform 1.7 or newer is required"
  if grep -q CHANGE_ME "$VAR_FILE"; then
    die "$VAR_FILE still contains CHANGE_ME. Set owner, team and cost_center (organisation tags)."
  fi
  aws sts get-caller-identity --query Account --output text >/dev/null 2>&1 \
    || die "no usable AWS credentials (run aws sso login, or export credentials)"
  info "account $(aws sts get-caller-identity --query Account --output text), region $REGION, environment $ENV_NAME"
}

init_terraform() {
  local args=(-input=false)
  if [ -n "${TF_STATE_BUCKET:-}" ] || [ -f "backend.$ENV_NAME.hcl" ]; then
    tf_version_ok 1.10 || die "remote state with S3 locking needs terraform 1.10 or newer"
    printf 'terraform {\n  backend "s3" {}\n}\n' > backend_override.tf # git-ignored; code has no backend of its own
    if [ -f "backend.$ENV_NAME.hcl" ]; then
      args+=(-backend-config="backend.$ENV_NAME.hcl")
    else
      args+=(-backend-config="bucket=$TF_STATE_BUCKET" -backend-config="key=live-assist/$ENV_NAME/terraform.tfstate"
             -backend-config="region=${TF_STATE_REGION:-$REGION}" -backend-config="encrypt=true"
             -backend-config="use_lockfile=true")
    fi
    info "remote state"
  else
    rm -f backend_override.tf
    warn "Using LOCAL state (set TF_STATE_BUCKET or create backend.$ENV_NAME.hcl, see backend.hcl.example)."
    warn "The state holds the generated access key; keep terraform.tfstate private and do not lose it."
  fi
  terraform init "${args[@]}" -reconfigure >/dev/null
}

tf() { terraform "$1" -var-file="$VAR_FILE" "${@:2}"; }

build_package() {
  log "Lambda package"
  mkdir -p build
  "$PYTHON" "$SRC_DIR/deploy/build_lambda.py" --out "$TF_DIR/$ZIP" | sed 's/^/    /'
}

in_state() { terraform state list 2>/dev/null | grep -qx "$1"; }

# --- console (Amplify manual deployment) ---------------------------------------------------

deploy_console() {
  need zip; need curl
  local app_id branch api_url stage zip_file created job_id upload_url status i
  app_id=$(terraform output -raw amplify_app_id 2>/dev/null) || die "the Amplify app does not exist yet; run up"
  branch=$(terraform output -raw amplify_branch)
  api_url=$(terraform output -raw api_endpoint_direct)
  [ -n "$app_id" ] && [ -n "$api_url" ] || die "Terraform outputs are empty; run up first"
  log "Console to Amplify ($app_id/$branch)"

  mkdir -p build
  stage=$(mktemp -d)
  zip_file="$TF_DIR/build/console.zip"
  cp -R "$SRC_DIR/console/." "$stage/"
  printf '// Written by deploy.sh.\nwindow.LIVE_ASSIST_CONFIG = { apiBase: "%s" };\n' "$api_url" > "$stage/config.js"
  rm -f "$zip_file"
  (cd "$stage" && zip -qr "$zip_file" .)
  rm -rf "$stage"

  created=$(aws amplify create-deployment --app-id "$app_id" --branch-name "$branch" \
    --query '[jobId,zipUploadUrl]' --output text)
  job_id=$(printf '%s' "$created" | cut -f1)
  upload_url=$(printf '%s' "$created" | cut -f2)
  curl -fsS -T "$zip_file" "$upload_url" >/dev/null || die "could not upload the console package"
  aws amplify start-deployment --app-id "$app_id" --branch-name "$branch" --job-id "$job_id" >/dev/null

  for i in $(seq 1 60); do
    status=$(aws amplify get-job --app-id "$app_id" --branch-name "$branch" --job-id "$job_id" \
      --query 'job.summary.status' --output text)
    case $status in
      SUCCEED) info "deployed: $(terraform output -raw console_url)"; return 0 ;;
      FAILED|CANCELLED) die "Amplify deployment $status (job $job_id)" ;;
    esac
    sleep 5
  done
  die "Amplify deployment still running after 5 minutes (job $job_id)"
}

# --- agent image ----------------------------------------------------------------------------

# Looked up in AWS, not from outputs: outputs are not refreshed by the targeted apply that creates the repository.
ecr_url() { aws ecr describe-repositories --repository-names "$NAME/agent" --query "repositories[0].repositoryUri" --output text 2>/dev/null || true; }

image_exists() { # image_exists <repo-name> <tag>
  aws ecr describe-images --repository-name "$1" --image-ids "imageTag=$2" >/dev/null 2>&1
}

build_and_push_image() {
  need docker "to build the agent image"
  docker info >/dev/null 2>&1 || die "docker is not running"
  local url repo registry tag
  url=$(ecr_url)
  [ -n "$url" ] || die "the ECR repository does not exist yet; run up"
  registry=${url%%/*}
  repo=${url#*/}
  tag="$(git -C "$SRC_DIR" rev-parse --short HEAD 2>/dev/null || echo build)-$(date -u +%Y%m%d%H%M%S)"
  log "Agent image $url:$tag"
  aws ecr get-login-password | docker login --username AWS --password-stdin "$registry" >/dev/null
  docker build --platform linux/arm64 -t "$url:$tag" "$SRC_DIR" >build/docker.log 2>&1 \
    || { tail -20 build/docker.log; die "docker build failed (full log: build/docker.log)"; }
  docker push "$url:$tag" >/dev/null
  image_exists "$repo" "$tag" || die "the pushed image was not found in ECR"
  set_tfvar agent_image_tag "$tag"
  info "pushed; agent_image_tag in $VAR_FILE is now $tag (commit that change)"
}

# --- long-term memory retention ------------------------------------------------------------

set_retention() {
  log "Long-term memory retention"
  "$PYTHON" -c 'import boto3' 2>/dev/null || die "$PYTHON has no boto3 (use PYTHON=$SRC_DIR/.venv/bin/python)"
  "$PYTHON" "$SRC_DIR/deploy/ensure_ltm_memory.py" --name "${NAME_US}_ltm" --region "$REGION" \
    --event-expiry-days "$RETENTION_DAYS" --record-expiry-days "$RETENTION_DAYS" >/dev/null \
    || die "could not set the long-term memory expiry"
  info "facts and events expire after $RETENTION_DAYS days"
}

# --- commands ---------------------------------------------------------------------------------

cmd_check() {
  need terraform
  init_terraform_offline
  log "terraform fmt"; terraform fmt -check -recursive
  log "terraform validate"; terraform validate
  log "terraform test (mocked provider)"; terraform test
  local tool
  for tool in tflint tfsec checkov; do
    if command -v "$tool" >/dev/null 2>&1; then
      log "$tool"
      case $tool in
        tflint) tflint --chdir=. ;;
        tfsec)  tfsec . --tfvars-file "$VAR_FILE" --no-colour --concise-output ;;
        checkov) checkov -d . --framework terraform --var-file "$VAR_FILE" --compact --quiet ;;
      esac
    else
      warn "$tool is not installed; the pipeline runs it, so install it to see the same result"
    fi
  done
}

init_terraform_offline() { terraform init -backend=false -input=false >/dev/null; }

cmd_plan() {
  preflight; init_terraform
  build_package
  log "Plan"
  tf plan
}

cmd_up() {
  preflight; init_terraform
  build_package
  if ! in_state aws_ecr_repository.agent; then
    log "Image registry first (the agent runtime needs an image to start)"
    tf apply -target=aws_ecr_repository.agent -target=aws_kms_key.main -target=aws_kms_alias.main
  fi
  local tag
  tag=$(tfvar agent_image_tag "")
  if [ -z "$tag" ] || ! image_exists "$NAME/agent" "$tag"; then
    build_and_push_image
  fi
  log "Apply"
  tf apply
  deploy_console
  set_retention
  cmd_outputs
}

cmd_image() {
  preflight; init_terraform
  in_state aws_ecr_repository.agent || die "nothing is deployed yet; run up"
  build_package
  build_and_push_image
  tf apply
}

cmd_update() {
  preflight; init_terraform
  build_package
  tf apply
  deploy_console
}

cmd_console() { preflight; init_terraform; deploy_console; }

cmd_retention() { preflight; set_retention; }

cmd_outputs() {
  init_terraform_offline_if_needed
  log "Outputs"
  info "Console:       $(terraform output -raw console_url 2>/dev/null || echo 'not deployed')"
  info "Agent image:   $(terraform output -raw agent_image_tag 2>/dev/null | sed 's/^$/(none yet: run image)/')"
  info "Runtime:       $(terraform output -raw agent_runtime_arn 2>/dev/null | sed 's/^$/(none yet)/')"
  info "Access key: aws secretsmanager get-secret-value --secret-id $(terraform output -raw access_key_secret_name 2>/dev/null || echo '<name>') --query SecretString --output text"
  local provider; provider="$(terraform output -raw meeting_provider 2>/dev/null || echo vexa)"
  if [ "$provider" = "recall" ]; then
    info "Meetings: Recall.ai. Put the Recall API key and verification secret in Secrets Manager (see README), then add"
    info "  $(terraform output -raw recall_status_webhook_url 2>/dev/null || echo '<recall_status_webhook_url>') in the Recall dashboard (bot.status_change)."
    info "Open the console, enter the access key when asked, then paste a meeting link."
  else
    info "Open the console, enter the access key when asked, then your Vexa key in the Meeting card and a meeting link."
  fi
}

init_terraform_offline_if_needed() { [ -d .terraform ] || init_terraform; }

cmd_down() {
  preflight; init_terraform
  printf 'This destroys every %s resource in %s (%s), including call data and uploads.\n' "$NAME" "$REGION" "$ENV_NAME"
  local answer
  read -r -p "Type the environment name ($ENV_NAME) to continue: " answer
  [ "$answer" = "$ENV_NAME" ] || die "cancelled"
  [ "$(tfvar environment "")" = "$ENV_NAME" ] || die "$VAR_FILE does not describe environment $ENV_NAME"
  build_package
  tf destroy
}

case $COMMAND in
  check)     cmd_check ;;
  plan)      cmd_plan ;;
  up)        cmd_up ;;
  image)     cmd_image ;;
  update)    cmd_update ;;
  console)   cmd_console ;;
  retention) cmd_retention ;;
  outputs)   cmd_outputs ;;
  down)      cmd_down ;;
  *)         usage ;;
esac
