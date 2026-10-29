#!/usr/bin/env bash
# Deploy the Discovery agent: agent zip -> S3 -> SAM stack -> CORS -> ui/config.js -> CSP -> Amplify -> smoke test
#   ./scripts/deploy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ---------------------------------------------------------------- config
[[ -f config.env ]] || { echo "config.env not found (cp config.env.example config.env)"; exit 1; }
set -a; source config.env; set +a
for var in AWS_REGION STACK_NAME RUNTIME_NAME EXTRACTION_MODEL_ID HIDDEN_SIGNALS_MODEL_ID SMALL_MODEL_ID UI_ORIGIN; do
  value="${!var:-}"
  if [[ -z "$value" || "$value" == "REPLACE_ME" || "$value" == \<* ]]; then
    echo "config.env: $var is not set"; exit 1
  fi
done
[[ -n "${AWS_PROFILE:-}" ]] || unset AWS_PROFILE
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

for tool in aws sam uv zip unzip curl shasum file; do
  command -v "$tool" >/dev/null || { echo "$tool is required"; exit 1; }
done
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "Deploying $STACK_NAME to account $ACCOUNT_ID ($AWS_REGION)"

# ---------------------------------------------------------------- 1. agent code zip -> S3 (artifacts bucket outside the stack)
BUCKET="ingram-discovery-artifacts-${ACCOUNT_ID}-${AWS_REGION}"
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

# Content-addressed key: CloudFormation only updates the Runtime when the code or the pins change.
HASH="$( (cat agent/requirements.txt agent/main.py; find agent/discovery_agent agent/schema -type f \
          -not -path '*/__pycache__/*' -not -name '.DS_Store' | LC_ALL=C sort | while read -r f; do echo "$f"; cat "$f"; done) \
        | shasum -a 256 | cut -c1-16)"
KEY="discovery-agent/agent-${HASH}.zip"
if aws s3api head-object --bucket "$BUCKET" --key "$KEY" >/dev/null 2>&1; then
  echo "Agent code s3://${BUCKET}/${KEY} already uploaded (code unchanged)"
else
  ZIP="$(./scripts/package_agent.sh)"
  aws s3 cp "$ZIP" "s3://${BUCKET}/${KEY}" --only-show-errors
  echo "Uploaded agent code to s3://${BUCKET}/${KEY}"
fi

# ---------------------------------------------------------------- 2. SAM stack
sam build --template-file template.yaml
sam deploy \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --s3-prefix "$STACK_NAME" \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --tags Project=ingram-discovery \
  --parameter-overrides \
    UiOrigin="$UI_ORIGIN" \
    AgentCodeBucket="$BUCKET" \
    AgentCodeKey="$KEY" \
    RuntimeName="$RUNTIME_NAME" \
    ExtractionModelId="$EXTRACTION_MODEL_ID" \
    HiddenSignalsModelId="$HIDDEN_SIGNALS_MODEL_ID" \
    SmallModelId="$SMALL_MODEL_ID" \
    InferenceProfilePattern="${INFERENCE_PROFILE_PATTERN:-us.anthropic.*}" \
    KmsKeyArn="${KMS_KEY_ARN:-}" \
    RunsPerDay="${RUNS_PER_DAY:-20}" \
    RunsPerIpHour="${RUNS_PER_IP_HOUR:-4}"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

# ---------------------------------------------------------------- 2b. CORS (API + data bucket): Amplify URL, UI_ORIGIN + CORS_EXTRA_ORIGINS
API_ID="$(aws cloudformation describe-stack-resource --stack-name "$STACK_NAME" --logical-resource-id DiscoveryHttpApi \
  --query StackResourceDetail.PhysicalResourceId --output text)"
UI_URL="$(output UiUrl)"
DATA_BUCKET="$(output DataBucketName)"
ORIGINS="$UI_URL,$UI_ORIGIN${CORS_EXTRA_ORIGINS:+,$CORS_EXTRA_ORIGINS}"
ORIGINS="$(echo "$ORIGINS" | tr ',' '\n' | sed -E 's#/+$##' | grep -v '^$' | sort -u | paste -sd, -)"
aws apigatewayv2 update-api --api-id "$API_ID" --cors-configuration \
  "AllowOrigins=$ORIGINS,AllowHeaders=content-type,AllowMethods=GET,POST,OPTIONS,MaxAge=600" >/dev/null
ORIGINS_JSON="$(echo "$ORIGINS" | tr ',' '\n' | sed 's/.*/"&"/' | paste -sd, -)"
aws s3api put-bucket-cors --bucket "$DATA_BUCKET" --cors-configuration "{\"CORSRules\":[{\"AllowedOrigins\":[${ORIGINS_JSON}],
  \"AllowedMethods\":[\"PUT\",\"GET\"],\"AllowedHeaders\":[\"content-type\",\"x-amz-server-side-encryption\",
  \"x-amz-server-side-encryption-aws-kms-key-id\"],\"ExposeHeaders\":[\"ETag\"],\"MaxAgeSeconds\":600}]}"
echo "CORS origins (API + data bucket): $ORIGINS"

# ---------------------------------------------------------------- 3. UI config
API_URL="$(output ApiUrl)"
DATA_ORIGIN="$(output DataOrigin)"
UI_APP_ID="$(output UiAppId)"
RUNTIME_ARN="$(output AgentRuntimeArn)"

cat > ui/config.js <<EOF
// Generated by scripts/deploy.sh. Safe to commit: contains no secrets.
window.DISCOVERY_CONFIG = {
  apiUrl: "${API_URL}",
};
EOF

# ---------------------------------------------------------------- 4. web UI -> Amplify Hosting (manual deployment)
UI_ZIP="$ROOT/build/ui.zip"
mkdir -p "$ROOT/build" && rm -f "$UI_ZIP"
# Pin the page's Content-Security-Policy to this exact API and the data bucket's regional host (the browser PUTs the
# documents straight to S3). A *.amazonaws.com wildcard does not match regional hosts.
STAGE_UI="$ROOT/build/ui" && rm -rf "$STAGE_UI" && cp -R ui "$STAGE_UI"
API_ORIGIN="${API_URL%/}"
CONNECT="connect-src 'self' ${API_ORIGIN} ${DATA_ORIGIN};"
sed -i.bak -E "s#connect-src 'self' [^;]*;#${CONNECT}#" "$STAGE_UI/index.html" && rm -f "$STAGE_UI/index.html.bak"
grep -qF "$CONNECT" "$STAGE_UI/index.html" || { echo "could not set CSP connect-src"; exit 1; }
(cd "$STAGE_UI" && zip -qr -X "$UI_ZIP" index.html styles.css app.js dom.js overview.js report.js pipeline.js config.js)
read -r JOB_ID UPLOAD_URL < <(aws amplify create-deployment --app-id "$UI_APP_ID" --branch-name main \
  --query "[jobId, zipUploadUrl]" --output text)
curl --fail --silent --show-error -X PUT -H "Content-Type: application/zip" --upload-file "$UI_ZIP" "$UPLOAD_URL"
aws amplify start-deployment --app-id "$UI_APP_ID" --branch-name main --job-id "$JOB_ID" >/dev/null
echo -n "Publishing UI (Amplify job $JOB_ID)"
STATUS=""
for _ in $(seq 1 60); do
  STATUS="$(aws amplify get-job --app-id "$UI_APP_ID" --branch-name main --job-id "$JOB_ID" \
    --query "job.summary.status" --output text)"
  case "$STATUS" in
    SUCCEED) echo " done"; break ;;
    FAILED|CANCELLED) echo " $STATUS"; exit 1 ;;
    *) echo -n "."; sleep 5 ;;
  esac
done
[[ "$STATUS" == "SUCCEED" ]] || { echo " timed out (status $STATUS)"; exit 1; }
rm -rf "$ROOT/build" "$ROOT/.aws-sam"

# ---------------------------------------------------------------- 5. smoke test: ping the runtime (0 model calls)
echo "Smoke test: ping on the deployed runtime"
uv run --quiet --no-project --python 3.12 --with boto3==1.43.114 \
  python tools/invoke_runtime.py --arn "$RUNTIME_ARN" --region "$AWS_REGION" ping \
  || echo "Smoke test failed: see CloudWatch logs $(output LogGroup)"

echo
echo "Done."
echo "  UI:      $UI_URL"
echo "  API:     $API_URL"
echo "  Runtime: $RUNTIME_ARN"
echo "  Data:    s3://$DATA_BUCKET"
echo "  Open API (no sign-in): ${RUNS_PER_IP_HOUR:-4} runs per IP per hour, ${RUNS_PER_DAY:-20} per day."
echo "Local UI (optional): python3 -m http.server ${UI_ORIGIN##*:} --directory ui   then open $UI_ORIGIN"
