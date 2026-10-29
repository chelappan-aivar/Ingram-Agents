#!/usr/bin/env bash
# Deploy the Architecture agent: agent image -> ECR -> SAM stack -> CORS -> ui/config.js -> Amplify -> smoke test
#   ./scripts/deploy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ---------------------------------------------------------------- config
[[ -f config.env ]] || { echo "config.env not found (cp config.env.example config.env)"; exit 1; }
set -a; source config.env; set +a
for var in AWS_REGION STACK_NAME ECR_REPOSITORY RUNTIME_NAME ADR_MODEL_ID UI_ORIGIN; do
  value="${!var:-}"
  if [[ -z "$value" || "$value" == "REPLACE_ME" || "$value" == \<* ]]; then
    echo "config.env: $var is not set"; exit 1
  fi
done
[[ -n "${AWS_PROFILE:-}" ]] || unset AWS_PROFILE
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

for tool in aws sam docker zip curl python3 shasum; do
  command -v "$tool" >/dev/null || { echo "$tool is required"; exit 1; }
done
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "Deploying $STACK_NAME to account $ACCOUNT_ID ($AWS_REGION)"

# ---------------------------------------------------------------- 1. ECR repository (outside the stack: the image must exist first)
if ! aws ecr describe-repositories --repository-names "$ECR_REPOSITORY" >/dev/null 2>&1; then
  echo "Creating ECR repository $ECR_REPOSITORY"
  aws ecr create-repository --repository-name "$ECR_REPOSITORY" --image-tag-mutability IMMUTABLE \
    --image-scanning-configuration scanOnPush=true --encryption-configuration encryptionType=AES256 \
    --tags Key=Project,Value=ingram-architecture >/dev/null
  aws ecr put-lifecycle-policy --repository-name "$ECR_REPOSITORY" --lifecycle-policy-text \
    '{"rules":[{"rulePriority":1,"description":"Keep the newest 10 images","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":10},"action":{"type":"expire"}}]}' >/dev/null
fi
REPO_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}"

# ---------------------------------------------------------------- 2. agent image (content-addressed tag: rebuilt only when the code changes)
HASH="$( (cat Dockerfile .dockerignore; find agent -type f \
          -not -path 'agent/tests/*' -not -path '*/__pycache__/*' -not -path '*.egg-info/*' -not -path '*/.pytest_cache/*' \
          -not -name '.DS_Store' | LC_ALL=C sort | while read -r f; do echo "$f"; cat "$f"; done) \
        | shasum -a 256 | cut -c1-16)"
TAG="src-${HASH}"
DIGEST="$(aws ecr describe-images --repository-name "$ECR_REPOSITORY" --image-ids imageTag="$TAG" \
          --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || true)"
if [[ "$DIGEST" != sha256:* ]]; then
  echo "Building $REPO_URI:$TAG (linux/arm64; the first build downloads draw.io, a few minutes)"
  aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO_URI%%/*}" >/dev/null
  docker buildx build --platform linux/arm64 --provenance=false -f Dockerfile -t "$REPO_URI:$TAG" --push .
  DIGEST="$(aws ecr describe-images --repository-name "$ECR_REPOSITORY" --image-ids imageTag="$TAG" \
            --query 'imageDetails[0].imageDigest' --output text)"
  [[ "$DIGEST" == sha256:* ]] || { echo "could not read the pushed image digest"; exit 1; }
else
  echo "Image $TAG already in ECR (code unchanged)"
fi
CONTAINER_URI="$REPO_URI@$DIGEST"
echo "Agent image: $CONTAINER_URI"

# ---------------------------------------------------------------- 3. SAM stack
sam build --template-file template.yaml
sam deploy \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --s3-prefix "$STACK_NAME" \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --tags Project=ingram-architecture \
  --parameter-overrides \
    UiOrigin="$UI_ORIGIN" \
    ContainerUri="$CONTAINER_URI" \
    EcrRepositoryName="$ECR_REPOSITORY" \
    RuntimeName="$RUNTIME_NAME" \
    AdrModelId="$ADR_MODEL_ID" \
    DiagramModelId="${DIAGRAM_MODEL_ID:-$ADR_MODEL_ID}" \
    InferenceProfilePattern="${INFERENCE_PROFILE_PATTERN:-$ADR_MODEL_ID}" \
    LayoutEngine="${LAYOUT_ENGINE:-elk}" \
    AssumptionsMode="${ASSUMPTIONS_MODE:-suggest}" \
    NamedServicesOnly="${NAMED_SERVICES_ONLY:-true}" \
    RunsPerDay="${RUNS_PER_DAY:-40}" \
    RunsPerIpHour="${RUNS_PER_IP_HOUR:-6}"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

# ---------------------------------------------------------------- 3b. CORS: Amplify URL, UI_ORIGIN + CORS_EXTRA_ORIGINS
API_ID="$(aws cloudformation describe-stack-resource --stack-name "$STACK_NAME" --logical-resource-id ArchitectureHttpApi \
  --query StackResourceDetail.PhysicalResourceId --output text)"
UI_URL="$(output UiUrl)"
ORIGINS="$UI_URL,$UI_ORIGIN${CORS_EXTRA_ORIGINS:+,$CORS_EXTRA_ORIGINS}"
ORIGINS="$(echo "$ORIGINS" | tr ',' '\n' | sed -E 's#/+$##' | grep -v '^$' | sort -u | paste -sd, -)"
aws apigatewayv2 update-api --api-id "$API_ID" --cors-configuration \
  "AllowOrigins=$ORIGINS,AllowHeaders=content-type,AllowMethods=GET,POST,OPTIONS,MaxAge=600" >/dev/null
echo "CORS origins: $ORIGINS"

# ---------------------------------------------------------------- 4. UI config
API_URL="$(output ApiUrl)"
UI_APP_ID="$(output UiAppId)"
RUNTIME_ARN="$(output AgentRuntimeArn)"

cat > ui/config.js <<EOF
// Generated by scripts/deploy.sh. Safe to commit: contains no secrets.
window.ARCH_CONFIG = {
  apiUrl: "${API_URL}",
};
EOF

# ---------------------------------------------------------------- 5. web UI -> Amplify Hosting (manual deployment)
UI_ZIP="$ROOT/build/ui.zip"
mkdir -p "$ROOT/build" && rm -f "$UI_ZIP"
# Pin the page's Content-Security-Policy to this exact API (a *.amazonaws.com wildcard does not match regional hosts).
STAGE_UI="$ROOT/build/ui" && rm -rf "$STAGE_UI" && cp -R ui "$STAGE_UI"
API_ORIGIN="${API_URL%/}"
sed -i.bak -E "s#connect-src 'self' [^;]*;#connect-src 'self' ${API_ORIGIN};#" "$STAGE_UI/index.html" && rm -f "$STAGE_UI/index.html.bak"
grep -q "connect-src 'self' ${API_ORIGIN};" "$STAGE_UI/index.html" || { echo "could not set CSP connect-src"; exit 1; }
(cd "$STAGE_UI" && zip -qr -X "$UI_ZIP" index.html styles.css app.js markdown.js config.js sample_srs.md)
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

# ---------------------------------------------------------------- 6. smoke test: draw the sample design (0 model calls)
PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY=python3
if "$PY" -c "import boto3" 2>/dev/null; then
  echo "Smoke test: drawing samples/sample_design.json on the deployed runtime"
  "$PY" tools/invoke.py --arn "$RUNTIME_ARN" --region "$AWS_REGION" --case diagram --out "$ROOT/.state/smoke" \
    || echo "Smoke test failed: see CloudWatch logs $(output LogGroup)"
else
  echo "Skipping the smoke test (no boto3; run ./deploy.sh setup)"
fi

echo
echo "Done."
echo "  UI:      $UI_URL"
echo "  API:     $API_URL"
echo "  Runtime: $RUNTIME_ARN"
echo "  Open API (no sign-in): ${RUNS_PER_IP_HOUR:-6} runs per IP per hour, ${RUNS_PER_DAY:-40} per day."
echo "Local UI (optional): python3 -m http.server ${UI_ORIGIN##*:} --directory ui   then open $UI_ORIGIN"
