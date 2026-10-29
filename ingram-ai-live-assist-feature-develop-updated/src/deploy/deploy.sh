#!/usr/bin/env bash
# Live Assist: create, update or delete every AWS resource for one environment.
#
#   deploy/deploy.sh <env> plan      read-only: show what exists and what `up` would create
#   deploy/deploy.sh <env> up        create or update everything (safe to re-run)
#   deploy/deploy.sh <env> image     rebuild and push the agent image, update the runtime
#   deploy/deploy.sh <env> code      rebuild and update both Lambda functions
#   deploy/deploy.sh <env> smoke     end-to-end test through the API
#   deploy/deploy.sh <env> outputs   print endpoints, names and how to read the access key
#   deploy/deploy.sh <env> down      delete everything (asks for confirmation)
#
# Reads deploy/config.<env>.env. Needs AWS CLI v2, Docker (able to build arm64),
# Python 3.11+ with pip, and openssl. Uses the AWS credentials in the environment.
set -euo pipefail

DEPLOY_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$DEPLOY_DIR/.." && pwd)
PYTHON=${PYTHON:-python3}
CALLS_INDEX=calls-by-created
ACCOUNT_CALLS_INDEX=calls-by-account

log()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    \033[33m%s\033[0m\n' "$*"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
usage() { sed -n '3,12p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

[ $# -eq 2 ] || usage
ENV_NAME=$1
COMMAND=$2
CONFIG="$DEPLOY_DIR/config.$ENV_NAME.env"
[ -f "$CONFIG" ] || die "missing $CONFIG (copy deploy/config.example.env)"
set -a
# shellcheck disable=SC1090
. "$CONFIG"
set +a

: "${PREFIX:?PREFIX is required}" "${REGION:?REGION is required}" "${MODEL_ID:?MODEL_ID is required}"
# One retention period for call data: DynamoDB TTL, memory events and facts, uploads.
DATA_RETENTION_DAYS=${DATA_RETENTION_DAYS:-180}
MEMORY_EVENT_EXPIRY_DAYS=${MEMORY_EVENT_EXPIRY_DAYS:-$DATA_RETENTION_DAYS}
UPLOAD_EXPIRY_DAYS=${UPLOAD_EXPIRY_DAYS:-$DATA_RETENTION_DAYS}
LOG_RETENTION_DAYS=${LOG_RETENTION_DAYS:-14}
RUNTIME_IDLE_TIMEOUT_SECONDS=${RUNTIME_IDLE_TIMEOUT_SECONDS:-900}
RUNTIME_MAX_LIFETIME_SECONDS=${RUNTIME_MAX_LIFETIME_SECONDS:-28800}
PROCESS_DEBOUNCE_SECONDS=${PROCESS_DEBOUNCE_SECONDS:-1.0}
API_THROTTLE_RATE=${API_THROTTLE_RATE:-20}
API_THROTTLE_BURST=${API_THROTTLE_BURST:-40}
CORS_ALLOW_ORIGINS=${CORS_ALLOW_ORIGINS:-}

printf '%s' "$PREFIX" | grep -Eq '^[a-z][a-z0-9-]{2,19}$' \
  || die "PREFIX must be 3-20 lowercase letters, digits or hyphens, starting with a letter"
[ "$MEMORY_EVENT_EXPIRY_DAYS" -ge 3 ] && [ "$MEMORY_EVENT_EXPIRY_DAYS" -le 365 ] \
  || die "MEMORY_EVENT_EXPIRY_DAYS must be 3-365"
[ "$DATA_RETENTION_DAYS" -ge 31 ] || die "DATA_RETENTION_DAYS must be at least 31 (AgentCore's shortest fact expiry)"

export AWS_PAGER=""
aws_() { aws --region "$REGION" "$@"; }

ACCOUNT_ID=$(aws_ sts get-caller-identity --query Account --output text 2>/dev/null) \
  || die "no usable AWS credentials (run aws sso login or set credentials)"

# --- names ----------------------------------------------------------------------
NAME_US=$(printf '%s' "$PREFIX" | tr '-' '_')   # AgentCore names allow letters, digits, '_'
ECR_REPO="$PREFIX/agent"
REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
SESSIONS_TABLE="$PREFIX-sessions"
TIMELINE_TABLE="$PREFIX-timeline"
BUCKET="$PREFIX-uploads-$ACCOUNT_ID-$REGION"
ACCESS_KEY_SECRET="$PREFIX/access-key"
MEMORY_NAME="${NAME_US}_memory"
LTM_MEMORY_NAME="${NAME_US}_ltm"   # Long-term memory across calls (facts per account/opportunity)
RUNTIME_NAME="${NAME_US}_runtime"
# AgentCore can only be passed roles named *BedrockAgentCore* (BedrockAgentCoreFullAccess).
RUNTIME_ROLE="$PREFIX-BedrockAgentCoreRuntime"
API_ROLE="$PREFIX-api"
REPLAYER_ROLE="$PREFIX-replayer"
API_FUNCTION="$PREFIX-api"
REPLAYER_FUNCTION="$PREFIX-replayer"
API_NAME="$PREFIX-api"
case "$MODEL_ID" in
  global.*|us.*|eu.*|apac.*|jp.*|au.*|ca.*|us-gov.*) FOUNDATION_MODEL_ID=${MODEL_ID#*.} ;;
  *) FOUNDATION_MODEL_ID=$MODEL_ID ;;
esac
OUTPUTS="$DEPLOY_DIR/outputs/$ENV_NAME.env"
role_arn() { printf 'arn:aws:iam::%s:role/%s' "$ACCOUNT_ID" "$1"; }

export ACCOUNT_ID REGION PREFIX ECR_REPO SESSIONS_TABLE TIMELINE_TABLE BUCKET ACCESS_KEY_SECRET \
  MEMORY_NAME LTM_MEMORY_NAME RUNTIME_NAME API_FUNCTION REPLAYER_FUNCTION MODEL_ID FOUNDATION_MODEL_ID

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# --- helpers --------------------------------------------------------------------

# render <template> <out>: substitute ${VARS} (unknown names fail) and validate the JSON.
render() {
  "$PYTHON" - "$DEPLOY_DIR/templates/$1" "$2" <<'PY'
import json, os, string, sys
text = string.Template(open(sys.argv[1]).read()).substitute(os.environ)
json.loads(text)
open(sys.argv[2], "w").write(text)
PY
}

# json_map KEY=VALUE...: print a JSON object.
json_map() {
  "$PYTHON" -c 'import json, sys; print(json.dumps(dict(a.split("=", 1) for a in sys.argv[1:])))' "$@"
}

# retry <tries> <delay> <command...>
retry() {
  local tries=$1 delay=$2 i
  shift 2
  for ((i = 1; i <= tries; i++)); do
    if "$@"; then return 0; fi
    [ "$i" -lt "$tries" ] && sleep "$delay"
  done
  return 1
}

# wait_for <what> <want-regex> <fail-regex> <tries> <delay> <command...>
wait_for() {
  local what=$1 want=$2 fail=$3 tries=$4 delay=$5 i value=""
  shift 5
  for ((i = 1; i <= tries; i++)); do
    value=$("$@" 2>/dev/null || true)
    if printf '%s' "$value" | grep -Eq "$want"; then return 0; fi
    if [ -n "$fail" ] && printf '%s' "$value" | grep -Eq "$fail"; then die "$what: $value"; fi
    sleep "$delay"
  done
  die "timed out waiting for $what (last status: ${value:-none})"
}

none_to_empty() { grep -v '^None$' || true; }

# --- lookups (read-only) --------------------------------------------------------

memory_id() {
  { aws_ bedrock-agentcore-control list-memories \
      --query "memories[?starts_with(id, '${MEMORY_NAME}-')].id | [0]" --output text 2>/dev/null || true; } | none_to_empty
}
ltm_memory_id() {
  { aws_ bedrock-agentcore-control list-memories \
      --query "memories[?starts_with(id, '${LTM_MEMORY_NAME}-')].id | [0]" --output text 2>/dev/null || true; } | none_to_empty
}
runtime_id() {
  { aws_ bedrock-agentcore-control list-agent-runtimes \
      --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" --output text 2>/dev/null \
      || true; } | none_to_empty
}
runtime_arn() {
  aws_ bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$1" --query agentRuntimeArn --output text
}
api_id() {
  { aws_ apigatewayv2 get-apis --query "Items[?Name=='${API_NAME}'].ApiId | [0]" --output text 2>/dev/null \
      || true; } | none_to_empty
}
function_arn() {
  { aws_ lambda get-function --function-name "$1" --query Configuration.FunctionArn --output text 2>/dev/null \
      || true; } | none_to_empty
}
exists_repo()   { aws_ ecr describe-repositories --repository-names "$ECR_REPO" >/dev/null 2>&1; }
exists_table()  { aws_ dynamodb describe-table --table-name "$1" >/dev/null 2>&1; }
exists_bucket() { aws_ s3api head-bucket --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT_ID" >/dev/null 2>&1; }
exists_secret() { aws_ secretsmanager describe-secret --secret-id "$ACCESS_KEY_SECRET" >/dev/null 2>&1; }
exists_role()   { aws iam get-role --role-name "$1" >/dev/null 2>&1; }

# --- steps ----------------------------------------------------------------------

preflight() {
  log "Pre-flight: account $ACCOUNT_ID, region $REGION, prefix $PREFIX"
  command -v docker >/dev/null || die "docker is required"
  docker info >/dev/null 2>&1 || die "docker is not running"
  command -v openssl >/dev/null || die "openssl is required"
  "$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null || die "$PYTHON must be Python 3.11+"
  aws_ bedrock-agentcore-control list-agent-runtimes --max-results 1 >/dev/null 2>&1 \
    || die "Bedrock AgentCore is not available in $REGION, or this identity cannot use it"
  info "Checking Bedrock access to $MODEL_ID"
  aws_ bedrock-runtime converse --model-id "$MODEL_ID" \
    --messages '[{"role":"user","content":[{"text":"ping"}]}]' --inference-config maxTokens=1 \
    >/dev/null 2>"$WORK/bedrock.err" \
    || die "cannot call $MODEL_ID: $(cat "$WORK/bedrock.err"). Enable model access in the Bedrock console."
}

ensure_ecr() {
  log "ECR repository $ECR_REPO"
  if exists_repo; then info "exists"; return; fi
  aws_ ecr create-repository --repository-name "$ECR_REPO" \
    --image-scanning-configuration scanOnPush=true --encryption-configuration encryptionType=AES256 >/dev/null
  info "created"
}

ensure_tables() {
  log "DynamoDB table $SESSIONS_TABLE"
  if exists_table "$SESSIONS_TABLE"; then info "exists"; else
    aws_ dynamodb create-table --table-name "$SESSIONS_TABLE" --billing-mode PAY_PER_REQUEST \
      --attribute-definitions AttributeName=session_id,AttributeType=S \
      --key-schema AttributeName=session_id,KeyType=HASH >/dev/null
    info "created"
  fi

  log "DynamoDB table $TIMELINE_TABLE"
  index_spec() { printf '{"IndexName":"%s","KeySchema":[{"AttributeName":"%s","KeyType":"HASH"},{"AttributeName":"created_at","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}}' "$1" "$2"; }
  local index
  index=$(index_spec "$CALLS_INDEX" gsi1pk)
  local attributes=(AttributeName=session_id,AttributeType=S AttributeName=sk,AttributeType=S
                    AttributeName=gsi1pk,AttributeType=S AttributeName=gsi2pk,AttributeType=S
                    AttributeName=created_at,AttributeType=S)
  if exists_table "$TIMELINE_TABLE"; then info "exists"; else
    aws_ dynamodb create-table --table-name "$TIMELINE_TABLE" --billing-mode PAY_PER_REQUEST \
      --attribute-definitions "${attributes[@]}" \
      --key-schema AttributeName=session_id,KeyType=HASH AttributeName=sk,KeyType=RANGE \
      --global-secondary-indexes "[$index,$(index_spec "$ACCOUNT_CALLS_INDEX" gsi2pk)]" >/dev/null
    info "created"
  fi
  aws_ dynamodb wait table-exists --table-name "$SESSIONS_TABLE"
  aws_ dynamodb wait table-exists --table-name "$TIMELINE_TABLE"

  # DynamoDB adds one index per update, so each is added and waited for in turn.
  local name_pk name pk
  for name_pk in "$CALLS_INDEX:gsi1pk" "$ACCOUNT_CALLS_INDEX:gsi2pk"; do
    name=${name_pk%%:*}; pk=${name_pk#*:}
    if ! aws_ dynamodb describe-table --table-name "$TIMELINE_TABLE" \
        --query 'Table.GlobalSecondaryIndexes[].IndexName' --output text | grep -qw "$name"; then
      info "adding index $name"
      aws_ dynamodb update-table --table-name "$TIMELINE_TABLE" --attribute-definitions "${attributes[@]}" \
        --global-secondary-index-updates "[{\"Create\":$(index_spec "$name" "$pk")}]" >/dev/null
    fi
    wait_for "index $name" '^ACTIVE$' '' 90 10 aws_ dynamodb describe-table --table-name "$TIMELINE_TABLE" \
      --query "Table.GlobalSecondaryIndexes[?IndexName=='$name'].IndexStatus | [0]" --output text
  done

  local ttl
  ttl=$(aws_ dynamodb describe-time-to-live --table-name "$TIMELINE_TABLE" \
    --query TimeToLiveDescription.TimeToLiveStatus --output text)
  case "$ttl" in
    ENABLED|ENABLING) info "TTL on expires_at: $ttl" ;;
    *) aws_ dynamodb update-time-to-live --table-name "$TIMELINE_TABLE" \
         --time-to-live-specification Enabled=true,AttributeName=expires_at >/dev/null
       info "TTL enabled on expires_at" ;;
  esac
}

ensure_bucket() {
  log "S3 bucket $BUCKET"
  if exists_bucket; then info "exists"; else
    if [ "$REGION" = us-east-1 ]; then
      aws_ s3api create-bucket --bucket "$BUCKET" >/dev/null
    else
      aws_ s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
    fi
    info "created"
  fi
  aws_ s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws_ s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  aws_ s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration \
    "{\"Rules\":[{\"ID\":\"expire-uploads\",\"Status\":\"Enabled\",\"Filter\":{\"Prefix\":\"uploads/\"},\"Expiration\":{\"Days\":$UPLOAD_EXPIRY_DAYS},\"AbortIncompleteMultipartUpload\":{\"DaysAfterInitiation\":1}}]}" >/dev/null
  render bucket-policy.json "$WORK/bucket-policy.json"
  aws_ s3api put-bucket-policy --bucket "$BUCKET" --policy "file://$WORK/bucket-policy.json"
  if [ -n "$CORS_ALLOW_ORIGINS" ]; then
    "$PYTHON" -c 'import json, sys; print(json.dumps({"CORSRules": [{"AllowedOrigins": [o.strip() for o in sys.argv[1].split(",") if o.strip()], "AllowedMethods": ["PUT"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 600}]}))' \
      "$CORS_ALLOW_ORIGINS" > "$WORK/bucket-cors.json"
    aws_ s3api put-bucket-cors --bucket "$BUCKET" --cors-configuration "file://$WORK/bucket-cors.json"
    info "CORS for uploads: $CORS_ALLOW_ORIGINS"
  else
    aws_ s3api delete-bucket-cors --bucket "$BUCKET" >/dev/null 2>&1 || true
  fi
  info "private, encrypted, TLS only, uploads expire after $UPLOAD_EXPIRY_DAYS days"
}

ensure_access_key() {
  log "Access key secret $ACCESS_KEY_SECRET"
  if exists_secret; then info "exists (value unchanged)"; return; fi
  (umask 077 && printf '%s' "$(openssl rand -hex 32)" > "$WORK/access-key")
  aws_ secretsmanager create-secret --name "$ACCESS_KEY_SECRET" \
    --description "Live Assist API key ($PREFIX)" --secret-string "file://$WORK/access-key" >/dev/null
  rm -f "$WORK/access-key"
  info "created (read it with: deploy/deploy.sh $ENV_NAME outputs)"
}

ensure_memory() {
  log "AgentCore Memory $MEMORY_NAME"
  MEMORY_ID=$(memory_id)
  if [ -n "$MEMORY_ID" ]; then
    info "exists: $MEMORY_ID"
    local current
    current=$(aws_ bedrock-agentcore-control get-memory --memory-id "$MEMORY_ID" \
      --query memory.eventExpiryDuration --output text)
    if [ "$current" != "$MEMORY_EVENT_EXPIRY_DAYS" ]; then
      aws_ bedrock-agentcore-control update-memory --memory-id "$MEMORY_ID" \
        --event-expiry-duration "$MEMORY_EVENT_EXPIRY_DAYS" >/dev/null
      info "event expiry: $current -> $MEMORY_EVENT_EXPIRY_DAYS days"
    fi
  else
    MEMORY_ID=$(aws_ bedrock-agentcore-control create-memory --name "$MEMORY_NAME" \
      --description "Live Assist conversation history ($PREFIX)" \
      --event-expiry-duration "$MEMORY_EVENT_EXPIRY_DAYS" --query memory.id --output text)
    info "created: $MEMORY_ID"
  fi
  wait_for "memory $MEMORY_ID" '^ACTIVE$' 'FAILED' 60 10 \
    aws_ bedrock-agentcore-control get-memory --memory-id "$MEMORY_ID" --query memory.status --output text
}

ensure_ltm_memory() {
  log "AgentCore Memory $LTM_MEMORY_NAME (long-term)"
  "$PYTHON" -c 'import boto3' 2>/dev/null \
    || die "$PYTHON has no boto3; run with PYTHON=.venv/bin/python (see deploy/ensure_ltm_memory.py)"
  LTM_MEMORY_ID=$("$PYTHON" "$DEPLOY_DIR/ensure_ltm_memory.py" --name "$LTM_MEMORY_NAME" --region "$REGION" \
    --event-expiry-days "$MEMORY_EVENT_EXPIRY_DAYS" --record-expiry-days "$DATA_RETENTION_DAYS") \
    || die "could not create the long-term memory"
  info "ready: $LTM_MEMORY_ID"
}
# image and code reuse the long-term memory made by `up`; without it, calls run without memory.
lookup_ltm_memory() {
  LTM_MEMORY_ID=$(ltm_memory_id)
  [ -n "$LTM_MEMORY_ID" ] || warn "long-term memory $LTM_MEMORY_NAME not found; run up to enable it"
}
ROLES_CREATED=0
ensure_role() {
  local role=$1 trust=$2 policy=$3
  log "IAM role $role"
  render "$trust" "$WORK/$role-trust.json"
  render "$policy" "$WORK/$role-policy.json"
  if exists_role "$role"; then
    aws iam update-assume-role-policy --role-name "$role" --policy-document "file://$WORK/$role-trust.json"
    info "exists, trust policy updated"
  else
    aws iam create-role --role-name "$role" --assume-role-policy-document "file://$WORK/$role-trust.json" \
      --description "Live Assist ($PREFIX)" >/dev/null
    ROLES_CREATED=1
    info "created"
  fi
  aws iam put-role-policy --role-name "$role" --policy-name access --policy-document "file://$WORK/$role-policy.json"
  info "permissions: templates/$policy"
}

ensure_roles() {
  ensure_role "$RUNTIME_ROLE" runtime-trust.json runtime-policy.json
  ensure_role "$API_ROLE" lambda-trust.json api-policy.json
  ensure_role "$REPLAYER_ROLE" lambda-trust.json replayer-policy.json
  if [ "$ROLES_CREATED" = 1 ]; then info "waiting for new roles to propagate"; sleep 15; fi
}

build_image() {
  local tag
  tag="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo build)-$(date -u +%Y%m%d%H%M%S)"
  IMAGE_URI="$REGISTRY/$ECR_REPO:$tag"
  log "Image $IMAGE_URI"
  aws_ ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
  docker build --platform linux/arm64 -t "$IMAGE_URI" "$ROOT" >"$WORK/docker-build.log" 2>&1 \
    || { tail -20 "$WORK/docker-build.log"; die "docker build failed"; }
  docker push "$IMAGE_URI" >/dev/null
  info "pushed"
}

ensure_runtime() {
  log "AgentCore Runtime $RUNTIME_NAME"
  [ -n "${MEMORY_ID:-}" ] || die "memory not found; run up first"
  local env_json artifact
  env_json=$(json_map AGENTCORE_MEMORY_ID="$MEMORY_ID" LONG_TERM_MEMORY_ID="${LTM_MEMORY_ID:-}" \
    BEDROCK_MODEL_ID="$MODEL_ID" \
    SESSION_TABLE_NAME="$SESSIONS_TABLE" TIMELINE_TABLE_NAME="$TIMELINE_TABLE" \
    PROCESS_DEBOUNCE_SECONDS="$PROCESS_DEBOUNCE_SECONDS" DATA_RETENTION_DAYS="$DATA_RETENTION_DAYS" LOG_LEVEL=INFO)
  artifact=$(json_map containerUri="$IMAGE_URI" | sed 's/^/{"containerConfiguration":/; s/$/}/')
  local common=(
    --agent-runtime-artifact "$artifact"
    --role-arn "$(role_arn "$RUNTIME_ROLE")"
    --network-configuration networkMode=PUBLIC
    --protocol-configuration serverProtocol=HTTP
    --lifecycle-configuration "idleRuntimeSessionTimeout=$RUNTIME_IDLE_TIMEOUT_SECONDS,maxLifetime=$RUNTIME_MAX_LIFETIME_SECONDS"
    --environment-variables "$env_json"
  )
  RUNTIME_ID=$(runtime_id)
  if [ -n "$RUNTIME_ID" ]; then
    aws_ bedrock-agentcore-control update-agent-runtime --agent-runtime-id "$RUNTIME_ID" "${common[@]}" >/dev/null
    info "updated: $RUNTIME_ID"
  else
    retry 6 10 aws_ bedrock-agentcore-control create-agent-runtime --agent-runtime-name "$RUNTIME_NAME" \
      --description "Live Assist agent ($PREFIX)" "${common[@]}" >/dev/null \
      || die "could not create the runtime"
    RUNTIME_ID=$(runtime_id)
    info "created: $RUNTIME_ID"
  fi
  wait_for "runtime $RUNTIME_ID" '^READY$' 'FAILED' 90 10 \
    aws_ bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" --query status --output text
  RUNTIME_ARN=$(runtime_arn "$RUNTIME_ID")
}

build_zip() {
  log "Lambda package"
  "$PYTHON" "$DEPLOY_DIR/build_lambda.py" --out "$WORK/functions.zip" | sed 's/^/    /'
}

ensure_log_group() {
  aws_ logs create-log-group --log-group-name "$1" >/dev/null 2>&1 || true
  aws_ logs put-retention-policy --log-group-name "$1" --retention-in-days "$LOG_RETENTION_DAYS"
}

# ensure_function <name> <handler> <timeout> <memory-mb> <role> <env-json>
ensure_function() {
  local name=$1 handler=$2 timeout=$3 memory=$4 role=$5 env=$6
  log "Lambda $name"
  ensure_log_group "/aws/lambda/$name"
  printf '{"Variables":%s}' "$env" > "$WORK/$name-env.json"
  if [ -n "$(function_arn "$name")" ]; then
    aws_ lambda update-function-code --function-name "$name" --zip-file "fileb://$WORK/functions.zip" >/dev/null
    aws_ lambda wait function-updated-v2 --function-name "$name"
    aws_ lambda update-function-configuration --function-name "$name" --handler "$handler" \
      --timeout "$timeout" --memory-size "$memory" --role "$(role_arn "$role")" \
      --environment "file://$WORK/$name-env.json" >/dev/null
    aws_ lambda wait function-updated-v2 --function-name "$name"
    info "updated"
  else
    # New roles take a few seconds before Lambda can assume them.
    retry 10 6 aws_ lambda create-function --function-name "$name" --runtime python3.12 --architectures arm64 \
      --handler "$handler" --role "$(role_arn "$role")" --zip-file "fileb://$WORK/functions.zip" \
      --timeout "$timeout" --memory-size "$memory" --environment "file://$WORK/$name-env.json" \
      >/dev/null 2>"$WORK/lambda.err" || { cat "$WORK/lambda.err" >&2; die "could not create $name"; }
    aws_ lambda wait function-active-v2 --function-name "$name"
    info "created"
  fi
}

ensure_functions() {
  [ -n "${RUNTIME_ARN:-}" ] || die "runtime not found; run up first"
  ensure_function "$REPLAYER_FUNCTION" replayer.lambda_handler.handler 900 512 "$REPLAYER_ROLE" \
    "$(json_map TIMELINE_TABLE_NAME="$TIMELINE_TABLE" AGENTCORE_RUNTIME_ARN="$RUNTIME_ARN" AGENTCORE_QUALIFIER=DEFAULT)"
  # Async invocations must never retry: a retried source would send its turns twice.
  aws_ lambda put-function-event-invoke-config --function-name "$REPLAYER_FUNCTION" \
    --maximum-retry-attempts 0 --maximum-event-age-in-seconds 3600 >/dev/null

  ensure_function "$API_FUNCTION" api.handler.handler 30 512 "$API_ROLE" \
    "$(json_map TIMELINE_TABLE_NAME="$TIMELINE_TABLE" SESSION_TABLE_NAME="$SESSIONS_TABLE" \
        UPLOAD_BUCKET="$BUCKET" ACCESS_KEY_SECRET="$ACCESS_KEY_SECRET" AGENTCORE_RUNTIME_ARN="$RUNTIME_ARN" \
        AGENTCORE_QUALIFIER=DEFAULT REPLAYER_FUNCTION="$REPLAYER_FUNCTION" \
        LONG_TERM_MEMORY_ID="${LTM_MEMORY_ID:-}" DATA_RETENTION_DAYS="$DATA_RETENTION_DAYS" LOG_LEVEL=INFO)"
  API_FUNCTION_ARN=$(function_arn "$API_FUNCTION")
}

ensure_api() {
  log "API Gateway $API_NAME"
  [ -n "${API_FUNCTION_ARN:-}" ] || die "API function not found; run up first"
  API_ID=$(api_id)
  if [ -n "$API_ID" ]; then info "exists: $API_ID"; else
    # Quick create: one $default route and an auto-deployed $default stage.
    API_ID=$(aws_ apigatewayv2 create-api --name "$API_NAME" --protocol-type HTTP --target "$API_FUNCTION_ARN" \
      --query ApiId --output text)
    info "created: $API_ID"
  fi
  local integration
  integration=$(aws_ apigatewayv2 get-integrations --api-id "$API_ID" --query 'Items[0].IntegrationId' --output text)
  aws_ apigatewayv2 update-integration --api-id "$API_ID" --integration-id "$integration" \
    --integration-uri "$API_FUNCTION_ARN" --payload-format-version 2.0 >/dev/null
  aws_ apigatewayv2 update-stage --api-id "$API_ID" --stage-name '$default' \
    --default-route-settings "ThrottlingRateLimit=$API_THROTTLE_RATE,ThrottlingBurstLimit=$API_THROTTLE_BURST" >/dev/null
  info "throttling: $API_THROTTLE_RATE req/s, burst $API_THROTTLE_BURST"

  if [ -n "$CORS_ALLOW_ORIGINS" ]; then
    "$PYTHON" -c 'import json, sys; print(json.dumps({"AllowOrigins": [o.strip() for o in sys.argv[1].split(",") if o.strip()], "AllowMethods": ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"], "AllowHeaders": ["content-type", "x-access-key", "if-none-match"], "ExposeHeaders": ["etag"], "MaxAge": 600}))' \
      "$CORS_ALLOW_ORIGINS" > "$WORK/api-cors.json"
    aws_ apigatewayv2 update-api --api-id "$API_ID" --cors-configuration "file://$WORK/api-cors.json" >/dev/null
    info "CORS: $CORS_ALLOW_ORIGINS"
  else
    aws_ apigatewayv2 delete-cors-configuration --api-id "$API_ID" >/dev/null 2>&1 || true
    info "CORS off (set CORS_ALLOW_ORIGINS when the React host is known)"
  fi

  aws_ lambda remove-permission --function-name "$API_FUNCTION" --statement-id apigateway-invoke >/dev/null 2>&1 || true
  aws_ lambda add-permission --function-name "$API_FUNCTION" --statement-id apigateway-invoke \
    --action lambda:InvokeFunction --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:$REGION:$ACCOUNT_ID:$API_ID/*" >/dev/null
  API_URL=$(aws_ apigatewayv2 get-api --api-id "$API_ID" --query ApiEndpoint --output text)
  info "endpoint: $API_URL"
}

write_outputs() {
  mkdir -p "$DEPLOY_DIR/outputs"
  cat > "$OUTPUTS" <<EOF
# Written by deploy.sh $ENV_NAME on $(date -u +%Y-%m-%dT%H:%M:%SZ). Contains no secrets.
ACCOUNT_ID=$ACCOUNT_ID
REGION=$REGION
PREFIX=$PREFIX
API_URL=$API_URL
ACCESS_KEY_SECRET=$ACCESS_KEY_SECRET
RUNTIME_ARN=$RUNTIME_ARN
MEMORY_ID=$MEMORY_ID
LONG_TERM_MEMORY_ID=${LTM_MEMORY_ID:-}
IMAGE_URI=$IMAGE_URI
SESSIONS_TABLE=$SESSIONS_TABLE
TIMELINE_TABLE=$TIMELINE_TABLE
UPLOAD_BUCKET=$BUCKET
API_FUNCTION=$API_FUNCTION
REPLAYER_FUNCTION=$REPLAYER_FUNCTION
EOF
  log "Outputs written to deploy/outputs/$ENV_NAME.env"
}

load_outputs() {
  [ -f "$OUTPUTS" ] || die "no outputs for $ENV_NAME; run up first"
  # shellcheck disable=SC1090
  . "$OUTPUTS"
}

smoke() {
  load_outputs
  log "Smoke test against $API_URL"
  local key
  key=$(aws_ secretsmanager get-secret-value --secret-id "$ACCESS_KEY_SECRET" --query SecretString --output text)
  ILA_ACCESS_KEY="$key" "$PYTHON" "$DEPLOY_DIR/smoke.py" "$API_URL"
}

show_outputs() {
  load_outputs
  sed -n '2,$p' "$OUTPUTS"
  echo
  echo "Access key (send as the X-Access-Key header):"
  echo "  aws secretsmanager get-secret-value --region $REGION --secret-id $ACCESS_KEY_SECRET --query SecretString --output text"
}

plan() {
  log "Plan for $ENV_NAME: account $ACCOUNT_ID, region $REGION, prefix $PREFIX (read-only)"
  local t
  for t in runtime-trust.json lambda-trust.json runtime-policy.json api-policy.json replayer-policy.json bucket-policy.json; do
    render "$t" "$WORK/$t"
  done
  info "templates render and are valid JSON"

  status() { if "$@"; then printf 'exists '; else printf 'create '; fi; }
  printf '    %-8s %-28s %s\n' "$(status exists_repo)" "ECR repository" "$ECR_REPO"
  printf '    %-8s %-28s %s\n' "$(status exists_table "$SESSIONS_TABLE")" "DynamoDB table" "$SESSIONS_TABLE"
  printf '    %-8s %-28s %s\n' "$(status exists_table "$TIMELINE_TABLE")" "DynamoDB table + indexes" "$TIMELINE_TABLE ($CALLS_INDEX, $ACCOUNT_CALLS_INDEX)"
  printf '    %-8s %-28s %s\n' "$(status exists_bucket)" "S3 bucket" "$BUCKET"
  printf '    %-8s %-28s %s\n' "$(status exists_secret)" "Secrets Manager secret" "$ACCESS_KEY_SECRET"
  printf '    %-8s %-28s %s\n' "$([ -n "$(memory_id)" ] && echo exists || echo create)" "AgentCore Memory" "$MEMORY_NAME"
  printf '    %-8s %-28s %s\n' "$([ -n "$(ltm_memory_id)" ] && echo update || echo create)" "AgentCore Memory (long-term)" "$LTM_MEMORY_NAME"
  local r
  for r in "$RUNTIME_ROLE" "$API_ROLE" "$REPLAYER_ROLE"; do
    printf '    %-8s %-28s %s\n' "$(status exists_role "$r")" "IAM role" "$r"
  done
  printf '    %-8s %-28s %s\n' "build" "Container image" "$REGISTRY/$ECR_REPO:<git-sha>-<time>"
  printf '    %-8s %-28s %s\n' "$([ -n "$(runtime_id)" ] && echo update || echo create)" "AgentCore Runtime" "$RUNTIME_NAME"
  printf '    %-8s %-28s %s\n' "$([ -n "$(function_arn "$REPLAYER_FUNCTION")" ] && echo update || echo create)" "Lambda (replayer)" "$REPLAYER_FUNCTION"
  printf '    %-8s %-28s %s\n' "$([ -n "$(function_arn "$API_FUNCTION")" ] && echo update || echo create)" "Lambda (API)" "$API_FUNCTION"
  printf '    %-8s %-28s %s\n' "$([ -n "$(api_id)" ] && echo update || echo create)" "API Gateway HTTP API" "$API_NAME"
  info "model: $MODEL_ID (foundation model $FOUNDATION_MODEL_ID)"
  info "CORS: ${CORS_ALLOW_ORIGINS:-off}"
}

down() {
  printf 'This deletes every %s resource in account %s (%s), including all calls and uploads.\n' \
    "$PREFIX" "$ACCOUNT_ID" "$REGION"
  local answer
  read -r -p "Type the environment name ($ENV_NAME) to continue: " answer
  [ "$answer" = "$ENV_NAME" ] || die "cancelled"

  deleted() { if "$@" >/dev/null 2>&1; then info "deleted"; else info "not found"; fi; }

  log "API Gateway $API_NAME"
  API_ID=$(api_id)
  if [ -n "$API_ID" ]; then deleted aws_ apigatewayv2 delete-api --api-id "$API_ID"; else info "not found"; fi

  local fn
  for fn in "$API_FUNCTION" "$REPLAYER_FUNCTION"; do
    log "Lambda $fn"
    deleted aws_ lambda delete-function --function-name "$fn"
    aws_ logs delete-log-group --log-group-name "/aws/lambda/$fn" >/dev/null 2>&1 || true
  done

  log "AgentCore Runtime $RUNTIME_NAME"
  RUNTIME_ID=$(runtime_id)
  if [ -n "$RUNTIME_ID" ]; then
    aws_ bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$RUNTIME_ID" >/dev/null
    local i
    for ((i = 0; i < 60; i++)); do [ -z "$(runtime_id)" ] && break; sleep 10; done
    local group
    for group in $(aws_ logs describe-log-groups --log-group-name-prefix "/aws/bedrock-agentcore/runtimes/$RUNTIME_ID" \
        --query 'logGroups[].logGroupName' --output text 2>/dev/null || true); do
      aws_ logs delete-log-group --log-group-name "$group" >/dev/null 2>&1 || true
    done
    info "deleted"
  else info "not found"; fi

  log "AgentCore Memory $MEMORY_NAME"
  MEMORY_ID=$(memory_id)
  if [ -n "$MEMORY_ID" ]; then deleted aws_ bedrock-agentcore-control delete-memory --memory-id "$MEMORY_ID"; else info "not found"; fi
  log "AgentCore Memory $LTM_MEMORY_NAME (long-term)"
  LTM_MEMORY_ID=$(ltm_memory_id)
  if [ -n "$LTM_MEMORY_ID" ]; then deleted aws_ bedrock-agentcore-control delete-memory --memory-id "$LTM_MEMORY_ID"; else info "not found"; fi

  local role
  for role in "$RUNTIME_ROLE" "$API_ROLE" "$REPLAYER_ROLE"; do
    log "IAM role $role"
    aws iam delete-role-policy --role-name "$role" --policy-name access >/dev/null 2>&1 || true
    deleted aws iam delete-role --role-name "$role"
  done

  log "ECR repository $ECR_REPO"
  deleted aws_ ecr delete-repository --repository-name "$ECR_REPO" --force

  local table
  for table in "$SESSIONS_TABLE" "$TIMELINE_TABLE"; do
    log "DynamoDB table $table"
    deleted aws_ dynamodb delete-table --table-name "$table"
  done

  log "S3 bucket $BUCKET"
  if exists_bucket; then
    aws_ s3 rm "s3://$BUCKET" --recursive --quiet
    deleted aws_ s3api delete-bucket --bucket "$BUCKET"
  else info "not found"; fi

  log "Access key secret $ACCESS_KEY_SECRET"
  deleted aws_ secretsmanager delete-secret --secret-id "$ACCESS_KEY_SECRET" --force-delete-without-recovery

  rm -f "$OUTPUTS"
  log "Done"
}

up() {
  preflight
  ensure_ecr
  ensure_tables
  ensure_bucket
  ensure_access_key
  ensure_memory
  ensure_ltm_memory
  ensure_roles
  build_image
  ensure_runtime
  build_zip
  ensure_functions
  ensure_api
  write_outputs
  smoke
  echo
  show_outputs
}

case "$COMMAND" in
  plan) plan ;;
  up) up ;;
  image)
    MEMORY_ID=$(memory_id)
    lookup_ltm_memory
    build_image
    ensure_runtime
    if [ -f "$OUTPUTS" ]; then
      sed -i.bak "s|^IMAGE_URI=.*|IMAGE_URI=$IMAGE_URI|" "$OUTPUTS" && rm -f "$OUTPUTS.bak"
    fi
    ;;
  code)
    RUNTIME_ID=$(runtime_id)
    [ -n "$RUNTIME_ID" ] || die "runtime not found; run up first"
    RUNTIME_ARN=$(runtime_arn "$RUNTIME_ID")
    lookup_ltm_memory
    build_zip
    ensure_functions
    ;;
  smoke) smoke ;;
  outputs) show_outputs ;;
  down) down ;;
  *) usage ;;
esac
