#!/usr/bin/env bash
# Architecture Agent: one script for the whole AWS deployment (see README.md in this folder).
#
#   ./deploy.sh setup              local venv (.venv) with the agent from agent/ + pytest
#   ./deploy.sh test               run agent/tests (no AWS calls)
#   ./deploy.sh serve              run the agent locally on :8080 from agent/ (no Docker; Ctrl-C to stop)
#   ./deploy.sh local-test [--full]  build the image and test it locally like AgentCore (no AWS resources)
#   ./deploy.sh preflight          read-only checks (tools, credentials, AgentCore, Bedrock access, tests)
#   ./deploy.sh deploy             full stack: image, runtime, API, worker, UI (runs scripts/deploy.sh)
#   ./deploy.sh smoke [--full]     invoke the deployed agent (default: 0 model calls; --full: 2 Bedrock calls)
#   ./deploy.sh status             stack outputs
#   ./deploy.sh destroy            delete the stack and every ECR image (asks first; not reversible)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # this folder is self-contained and is the build context
AGENT_DIR="$HERE/agent"                               # the agent source code, vendored tools and tests
VENV="$HERE/.venv"                                    # local Python env for tests and tools (./deploy.sh setup)
STATE_DIR="$HERE/.state"
CONFIG_FILE="${CONFIG_FILE:-$HERE/config.env}"

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }
require() { for c in "$@"; do command -v "$c" >/dev/null 2>&1 || die "'$c' is required but not installed"; done; }

# ------------------------------------------------------------------ config
load_config() {
  local file="$CONFIG_FILE"
  if [[ ! -f "$file" ]]; then
    [[ "${1:-}" == "optional" ]] || die "Missing $file. Run: cp config.env.example config.env  (then edit it)"
    file="$HERE/config.env.example"
  fi
  # shellcheck disable=SC1090
  set -a; source "$file"; set +a
  : "${AWS_REGION:?set AWS_REGION in config.env}"
  : "${PROJECT_NAME:=ingram-architecture-agent}" "${RUNTIME_NAME:=ingram_architecture_agent}" "${STACK_NAME:=ingram-architecture}"
  export AWS_DEFAULT_REGION="$AWS_REGION"
  if [[ -n "${AWS_PROFILE:-}" ]]; then export AWS_PROFILE; fi
  PY="$VENV/bin/python"; [[ -x "$PY" ]] || PY=python3
}

setup() {   # local venv with the agent installed (editable) + pytest; used by preflight, smoke and local-test
  require python3
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die "Python 3.11+ is required"
  [[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -e "$AGENT_DIR[dev]"
  log "Ready: $VENV (agent installed from agent/). Run ./deploy.sh test"
}

run_tests() {
  [[ -x "$VENV/bin/python" ]] || die "No $VENV; run ./deploy.sh setup first"
  (cd "$AGENT_DIR" && "$VENV/bin/python" -m pytest -q -p no:warnings "$@")
}

aws_() { aws --region "$AWS_REGION" "$@"; }
output() { aws_ cloudformation describe-stacks --stack-name "$STACK_NAME" \
             --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null || true; }

# ------------------------------------------------------------------ steps
preflight() {
  local fail=0
  chk() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then log "ok   $d"; else warn "FAIL $d"; fail=1; fi; }
  chk "aws CLI v2 installed" aws --version
  chk "aws CLI knows bedrock-agentcore-control (else upgrade to the latest AWS CLI v2)" \
      aws bedrock-agentcore-control list-agent-runtimes --generate-cli-skeleton input
  chk "docker daemon running" docker info
  chk "docker buildx available (builds linux/arm64)" docker buildx version
  if ident=$(aws_ sts get-caller-identity --query Arn --output text 2>&1); then log "ok   credentials: $ident"
  else warn "FAIL credentials: $ident  (aws sso login?)"; fail=1; fi
  chk "AgentCore Runtime available in $AWS_REGION" aws_ bedrock-agentcore-control list-agent-runtimes --max-results 1
  local m
  for m in $(printf '%s\n' "${ADR_MODEL_ID:-}" "${DIAGRAM_MODEL_ID:-}" | sort -u); do
    chk "Bedrock inference profile $m" aws_ bedrock get-inference-profile --inference-profile-identifier "$m"
  done
  if [[ "${NETWORK_MODE:-PUBLIC}" == VPC && ( -z "${VPC_SUBNET_IDS:-}" || -z "${VPC_SECURITY_GROUP_IDS:-}" ) ]]; then
    warn "FAIL NETWORK_MODE=VPC needs VPC_SUBNET_IDS and VPC_SECURITY_GROUP_IDS"; fail=1
  fi
  if [[ -x "$VENV/bin/python" ]]; then
    if run_tests >/tmp/arch-agent-tests.log 2>&1; then
      log "ok   test suite (agent/tests)"; else warn "FAIL test suite (see /tmp/arch-agent-tests.log)"; fail=1; fi
  else warn "skip test suite (run ./deploy.sh setup to enable)"; fi
  [[ $fail -eq 0 ]] && log "Preflight passed." || die "Preflight found problems (above)."
}

build_image() {    # build_image <tag> [--push|--load]
  docker buildx build --platform linux/arm64 -f "$HERE/Dockerfile" -t "$1" --provenance=false "$2" "$HERE"
}

status() {
  aws_ cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query 'Stacks[0].{Status:StackStatus,Outputs:Outputs[].[OutputKey,OutputValue]}' --output table
}

deploy() {   # the full stack (UI, API, worker, runtime, table, bucket) is deployed by scripts/deploy.sh
  exec "$HERE/scripts/deploy.sh"
}

smoke() {   # smoke diagram|full
  local arn; arn="$(output AgentRuntimeArn)"
  [[ -n "$arn" && "$arn" != None ]] || die "No runtime in stack $STACK_NAME; run ./deploy.sh deploy"
  log "Invoking $arn (case: $1)"
  "$PY" "$HERE/tools/invoke.py" --arn "$arn" --region "$AWS_REGION" --case "$1" --out "$STATE_DIR/smoke"
}

local_test() {   # local_test [--full]
  require docker curl
  local name="${PROJECT_NAME}-local" port="${LOCAL_PORT:-8080}"
  log "Building ${PROJECT_NAME}:local (linux/arm64)"
  build_image "${PROJECT_NAME}:local" --load
  docker rm -f "$name" >/dev/null 2>&1 || true
  local env_args=(-e AWS_REGION="$AWS_REGION" -e LAYOUT_ENGINE="${LAYOUT_ENGINE:-elk}"
                  -e ASSUMPTIONS_MODE="${ASSUMPTIONS_MODE:-suggest}" -e DATA_DIR=/tmp/data)
  if [[ "${1:-}" == "--full" ]]; then   # short-lived local credentials, passed at run time, never baked in
    eval "$(aws configure export-credentials --format env)" || die "no local AWS credentials for --full"
    env_args+=(-e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN)
  fi
  log "Running $name on :$port (read-only root filesystem, writable /tmp, like AgentCore)"
  docker run -d --name "$name" --platform linux/arm64 -p "$port:8080" --read-only --tmpfs /tmp:rw,size=1g \
    "${env_args[@]}" "${PROJECT_NAME}:local" >/dev/null
  trap 'docker rm -f "'"$name"'" >/dev/null 2>&1 || true' EXIT
  for _ in $(seq 1 60); do curl -fsS "http://localhost:$port/ping" >/dev/null 2>&1 && break; sleep 1; done
  curl -fsS "http://localhost:$port/ping" >/dev/null || { docker logs "$name" | tail -40; die "no answer on /ping"; }
  log "/ping ok"
  "$PY" "$HERE/tools/invoke.py" --local "http://localhost:$port" --case diagram --out "$STATE_DIR/local" \
    || { docker logs "$name" | tail -60; die "diagram case failed"; }
  if [[ "${1:-}" == "--full" ]]; then
    "$PY" "$HERE/tools/invoke.py" --local "http://localhost:$port" --case full --out "$STATE_DIR/local" \
      || { docker logs "$name" | tail -60; die "full case failed"; }
  fi
  log "Local test passed. Outputs in deployment/.state/local/"
}

destroy() {
  require aws
  local repo; repo="$(output RepositoryName)"
  warn "This deletes stack '$STACK_NAME' in $AWS_REGION (account $(aws_ sts get-caller-identity --query Account --output text)):"
  warn "the AgentCore Runtime, API, worker, UI and EVERY image in ECR repo '$repo'."
  warn "The runs table and data bucket are retained (delete them by hand if you no longer need them)."
  warn "Callers will start failing. This is not reversible (redeploy with ./deploy.sh deploy)."
  read -r -p "Type the stack name '$STACK_NAME' to continue: " ans
  [[ "$ans" == "$STACK_NAME" ]] || die "Cancelled."
  if [[ -n "$repo" && "$repo" != None ]]; then
    local ids; ids="$(aws_ ecr list-images --repository-name "$repo" --query imageIds --output json)"
    [[ "$ids" == "[]" ]] || aws_ ecr batch-delete-image --repository-name "$repo" --image-ids "$ids" >/dev/null
  fi
  aws_ cloudformation delete-stack --stack-name "$STACK_NAME"
  aws_ cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
  rm -f "$STATE_DIR/image_uri"
  log "Deleted $STACK_NAME"
}

# ------------------------------------------------------------------ main
cmd="${1:-help}"; shift || true
case "$cmd" in
  setup)      setup ;;
  test)       run_tests "$@" ;;
  serve)      load_config optional; [[ -x "$VENV/bin/python" ]] || die "run ./deploy.sh setup first"
              log "Agent on http://localhost:${LOCAL_PORT:-8080} (from agent/). Try: .venv/bin/python tools/invoke.py --local http://localhost:${LOCAL_PORT:-8080}"
              (cd "$AGENT_DIR" && DATA_DIR=/tmp/arch-agent-data exec "$VENV/bin/python" -m architecture_agent.agent) ;;
  preflight)  load_config; preflight ;;
  deploy)     load_config; deploy ;;
  smoke)      load_config; [[ "${1:-}" == "--full" ]] && smoke full || smoke diagram ;;
  status)     load_config; status ;;
  local-test) load_config optional; local_test "${1:-}" ;;
  destroy)    load_config; destroy ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; [[ "$cmd" == help ]] || exit 2 ;;
esac
