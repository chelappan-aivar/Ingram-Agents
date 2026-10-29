#!/usr/bin/env bash
# Deploy the Intake agent: agent zip -> SAM stack -> ui/config.js -> Amplify Hosting (Intake UI + Funding Desk)
#   ./scripts/deploy.sh
#   UI_ONLY=1 ./scripts/deploy.sh    republish the web UI only (the stack must already exist)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ---------------------------------------------------------------- config
[[ -f config.env ]] || { echo "config.env not found (copy config.env.example)"; exit 1; }
set -a; source config.env; set +a
for var in AWS_REGION STACK_NAME ENTRA_TENANT_ID ENTRA_CLIENT_ID BEDROCK_MODEL_ID MAX_EMAILS UI_ORIGIN; do
  value="${!var:-}"
  if [[ -z "$value" || "$value" == "REPLACE_ME" || "$value" == \<* ]]; then
    echo "config.env: $var is not set"; exit 1
  fi
done
unset ENTRA_CLIENT_SECRET   # not used: sign-in is the Microsoft device-code flow (public client)
DYNAMICS_URL="${DYNAMICS_URL:-}"; DYNAMICS_URL="${DYNAMICS_URL%/}"
DYNAMICS_SECRET_ARN=""
[[ -n "${AWS_PROFILE:-}" ]] || unset AWS_PROFILE
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

for tool in aws sam uv zip curl; do
  command -v "$tool" >/dev/null || { echo "$tool is required"; exit 1; }
done
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "Deploying $STACK_NAME to account $ACCOUNT_ID ($AWS_REGION)"

if [[ "${UI_ONLY:-0}" != "1" ]]; then
# ---------------------------------------------------------------- 1. agent code zip -> S3
BUCKET="ingram-intake-artifacts-${ACCOUNT_ID}-${AWS_REGION}"
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "Creating artifact bucket $BUCKET"
  if [[ "$AWS_REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION" >/dev/null
  fi
  aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi

# ---------------------------------------------------------------- 1a. Dynamics 365 client secret -> Secrets Manager
MOCK_DYNAMICS=false
if [[ "$DYNAMICS_URL" == "mock" ]]; then
  MOCK_DYNAMICS=true
  DYNAMICS_URL=""
  echo "Funding check: mock Dynamics 365 (Dataverse Web API on Lambda + dummy tables; seed with scripts/seed_dynamics.py)"
elif [[ -n "$DYNAMICS_URL" ]]; then
  [[ "$DYNAMICS_URL" == https://* ]] || { echo "config.env: DYNAMICS_URL must start with https://"; exit 1; }
  [[ -n "${DYNAMICS_CLIENT_ID:-}" ]] || { echo "config.env: DYNAMICS_CLIENT_ID is required with DYNAMICS_URL"; exit 1; }
  SECRET_NAME="ingram-intake/dynamics-client-secret"
  if [[ -n "${DYNAMICS_CLIENT_SECRET:-}" && "$DYNAMICS_CLIENT_SECRET" != "REPLACE_ME" ]]; then
    if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" >/dev/null 2>&1; then
      aws secretsmanager put-secret-value --secret-id "$SECRET_NAME" --secret-string "$DYNAMICS_CLIENT_SECRET" >/dev/null
    else
      aws secretsmanager create-secret --name "$SECRET_NAME" --description "Dynamics 365 app client secret (funding check)" \
        --secret-string "$DYNAMICS_CLIENT_SECRET" >/dev/null
    fi
  fi
  DYNAMICS_SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --query ARN --output text 2>/dev/null)" \
    || { echo "Set DYNAMICS_CLIENT_SECRET in config.env (stored in Secrets Manager as $SECRET_NAME)"; exit 1; }
  echo "Funding check: live Dynamics 365 at $DYNAMICS_URL"
else
  echo "Funding check: demo store (DYNAMICS_URL is empty)"
fi
unset DYNAMICS_CLIENT_SECRET

ZIP="$(./scripts/package_agent.sh)"
# Content-addressed key: CloudFormation only updates the Runtime when the code changes.
HASH="$( (cat agent/requirements.txt agent/main.py; find agent/intake agent/funding shared/python -type f -not -path '*/__pycache__/*' | sort | xargs cat) | shasum -a 256 | cut -c1-16)"
KEY="intake-agent/agent-${HASH}.zip"
aws s3 cp "$ZIP" "s3://${BUCKET}/${KEY}" --only-show-errors
echo "Uploaded agent code to s3://${BUCKET}/${KEY}"

# ---------------------------------------------------------------- 2. SAM stack
# SAM rejects empty override values: pass the Dynamics settings only when set (template defaults are empty).
DYNAMICS_PARAMS=()
[[ -n "$DYNAMICS_URL" ]] && DYNAMICS_PARAMS+=("DynamicsUrl=$DYNAMICS_URL")
[[ -n "${DYNAMICS_TENANT_ID:-}" ]] && DYNAMICS_PARAMS+=("DynamicsTenantId=$DYNAMICS_TENANT_ID")
[[ -n "${DYNAMICS_CLIENT_ID:-}" ]] && DYNAMICS_PARAMS+=("DynamicsClientId=$DYNAMICS_CLIENT_ID")
[[ -n "$DYNAMICS_SECRET_ARN" ]] && DYNAMICS_PARAMS+=("DynamicsSecretArn=$DYNAMICS_SECRET_ARN")

sam build --template-file template.yaml
sam deploy \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --s3-prefix "$STACK_NAME" \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    EntraTenantId="$ENTRA_TENANT_ID" \
    EntraClientId="$ENTRA_CLIENT_ID" \
    UiOrigin="$UI_ORIGIN" \
    BedrockModelId="$BEDROCK_MODEL_ID" \
    MaxEmails="$MAX_EMAILS" \
    DynamicsWriteBack="${DYNAMICS_WRITE_BACK:-true}" \
    MockDynamics="$MOCK_DYNAMICS" \
    AgentCodeBucket="$BUCKET" \
    AgentCodeKey="$KEY" \
    ${DYNAMICS_PARAMS[@]+"${DYNAMICS_PARAMS[@]}"}

# ---------------------------------------------------------------- 2b. CORS: Amplify URL, UI_ORIGIN + CORS_EXTRA_ORIGINS
API_ID="$(aws cloudformation describe-stack-resource --stack-name "$STACK_NAME" --logical-resource-id IntakeHttpApi \
  --query StackResourceDetail.PhysicalResourceId --output text)"
AMPLIFY_URL="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='UiUrl'].OutputValue" --output text)"
DESK_ORIGIN="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='DeskUrl'].OutputValue" --output text)"
ORIGINS="$AMPLIFY_URL,$DESK_ORIGIN,$UI_ORIGIN${CORS_EXTRA_ORIGINS:+,$CORS_EXTRA_ORIGINS}"
ORIGINS="$(echo "$ORIGINS" | tr ',' '\n' | sed -E 's#/+$##' | grep -v '^$' | sort -u | paste -sd, -)"
aws apigatewayv2 update-api --api-id "$API_ID" --cors-configuration \
  "AllowOrigins=$ORIGINS,AllowHeaders=authorization,content-type,AllowMethods=GET,POST,OPTIONS,MaxAge=600" >/dev/null
echo "CORS origins: $ORIGINS"

else
  echo "UI_ONLY=1: skipping the agent code, the stack and CORS"
fi

# ---------------------------------------------------------------- 3. UI config
API_URL="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)"
UI_URL="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='UiUrl'].OutputValue" --output text)"
UI_APP_ID="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='UiAppId'].OutputValue" --output text)"

cat > ui/config.js <<EOF
// Generated by scripts/deploy.sh. Safe to commit: contains no secrets.
window.INTAKE_CONFIG = {
  apiUrl: "${API_URL}",
};
EOF

python3 scripts/gen_fields_js.py   # UI field catalogue = agent/intake/fields.py
python3 scripts/gen_form_js.py     # funding page form = shared/python/opportunity_form.py

# Sample files for the CRM / Seller notes boxes (listed in ui/samples.js)
mkdir -p ui/samples
cp ../test-data/crm-records.csv ../test-data/crm-records.xlsx ../test-data/seller-notes-*.docx ../test-data/seller-notes-*.pdf ui/samples/

# ---------------------------------------------------------------- 4. web UIs -> Amplify Hosting (manual deployments)
API_ORIGIN="${API_URL%/}"
# publish <app id> <name> <staged dir> <html pages...>: pins the CSP to this exact API, zips, uploads, waits.
publish() {
  local app_id="$1" name="$2" stage="$3"; shift 3
  local zip="$ROOT/build/$name.zip" page job url status=""
  for page in "$@"; do
    # A *.amazonaws.com wildcard would be too broad: connect-src is this API only.
    sed -i.bak -E "s#connect-src 'self' [^;]*;#connect-src 'self' ${API_ORIGIN};#" "$stage/$page" && rm -f "$stage/$page.bak"
    grep -q "connect-src 'self' ${API_ORIGIN};" "$stage/$page" || { echo "could not set CSP connect-src in $name/$page"; exit 1; }
  done
  rm -f "$zip"
  (cd "$stage" && zip -qr -X "$zip" .)
  read -r job url < <(aws amplify create-deployment --app-id "$app_id" --branch-name main \
    --query "[jobId, zipUploadUrl]" --output text)
  curl --fail --silent --show-error -X PUT -H "Content-Type: application/zip" --upload-file "$zip" "$url"
  aws amplify start-deployment --app-id "$app_id" --branch-name main --job-id "$job" >/dev/null
  echo -n "Publishing $name (Amplify job $job)"
  for _ in $(seq 1 60); do
    status="$(aws amplify get-job --app-id "$app_id" --branch-name main --job-id "$job" --query "job.summary.status" --output text)"
    case "$status" in
      SUCCEED) echo " done"; return 0 ;;
      FAILED|CANCELLED) echo " $status"; exit 1 ;;
      *) echo -n "."; sleep 5 ;;
    esac
  done
  echo " timed out (status $status)"; exit 1
}

mkdir -p "$ROOT/build"
STAGE_UI="$ROOT/build/ui" && rm -rf "$STAGE_UI" && mkdir -p "$STAGE_UI"
(cd ui && cp -R index.html styles.css app.js config.js fields.js samples.js samples \
  funding.html funding.js funding.css form.js "$STAGE_UI/")
publish "$UI_APP_ID" ui "$STAGE_UI" index.html funding.html

# Funding Desk: its own pages + the shared theme, API config and form schema (labels for the form answers).
DESK_APP_ID="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='DeskAppId'].OutputValue" --output text)"
DESK_URL="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='DeskUrl'].OutputValue" --output text)"
STAGE_DESK="$ROOT/build/desk" && rm -rf "$STAGE_DESK" && mkdir -p "$STAGE_DESK"
cp ui-desk/index.html ui-desk/desk.js ui-desk/desk.css ui/styles.css ui/config.js ui/form.js "$STAGE_DESK/"
publish "$DESK_APP_ID" desk "$STAGE_DESK" index.html

echo
echo "Done."
echo "  UI:  $UI_URL"
echo "  Funding check: ${UI_URL%/}/funding.html"
echo "  Funding Desk:  $DESK_URL"
echo "  API: $API_URL"
echo
echo "Sign-in uses the Microsoft device code; the Entra app needs 'Allow public client flows' = Yes."
echo "Local UI (optional): python3 -m http.server ${UI_ORIGIN##*:} --directory ui   then open $UI_ORIGIN"
