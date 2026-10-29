#!/usr/bin/env bash
# Run Live Assist on this machine: console + API + agent, with a local stand-in for DynamoDB.
#
#   scripts/dev_up.sh                  # then open http://127.0.0.1:8090
#   (for the "Meeting link" source, type your Vexa key in the console; nothing is read from the environment)
#
# Needs AWS credentials that can call Amazon Bedrock (the agent's model). Nothing else in AWS is used:
# the two DynamoDB tables live in a throwaway local server (moto), so calls are forgotten on restart.
# Audio recordings need real S3 and Amazon Transcribe, so that source works only against a deployed environment.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
DDB_PORT="${DDB_PORT:-5050}"
AGENT_PORT=8080
CONSOLE_PORT="${CONSOLE_PORT:-8090}"

if [ ! -x "$PY" ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements-dev.txt "moto[server]==5.2.3" 2>&1 | grep -v "notice" || true

for port in "$DDB_PORT" "$AGENT_PORT" "$CONSOLE_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Stop what is using it, or set DDB_PORT / CONSOLE_PORT." >&2
    exit 1
  fi
done

export AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_ENDPOINT_URL_DYNAMODB="http://127.0.0.1:${DDB_PORT}"   # DynamoDB only; Bedrock stays real
export SESSION_TABLE_NAME="${SESSION_TABLE_NAME:-ila-local-sessions}"
export TIMELINE_TABLE_NAME="${TIMELINE_TABLE_NAME:-ila-local-timeline}"
export ACCESS_KEY="${ACCESS_KEY:-dev}"                             # local only; the console adds it itself
export AGENT_URL="http://127.0.0.1:${AGENT_PORT}"
export PROMPT_LOG_DIR=""                                           # no dumps of call text to disk

# The credentials moto sees are irrelevant; botocore still needs some to sign requests.
if ! "$PY" - <<'EOF' >/dev/null 2>&1
import boto3, sys
boto3.client("sts").get_caller_identity()
EOF
then
  echo "WARNING: AWS credentials are missing or expired. The console will start, but the agent cannot call Bedrock" >&2
  echo "         until you refresh them (for example: aws sso login, or export fresh AWS_ACCESS_KEY_ID/...)." >&2
fi

pids=()
cleanup() { for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

.venv/bin/moto_server -H 127.0.0.1 -p "$DDB_PORT" >/dev/null 2>&1 &
pids+=($!)
for _ in $(seq 1 40); do
  curl -s -o /dev/null "http://127.0.0.1:${DDB_PORT}/moto-api/" && break
  sleep 0.25
done

"$PY" scripts/create_session_table.py --table-name "$SESSION_TABLE_NAME" >/dev/null
"$PY" scripts/create_timeline_table.py --table-name "$TIMELINE_TABLE_NAME" >/dev/null
echo "Local tables ready ($SESSION_TABLE_NAME, $TIMELINE_TABLE_NAME)."

"$PY" main.py >/tmp/live-assist-agent.log 2>&1 &
pids+=($!)
for _ in $(seq 1 60); do
  curl -s -o /dev/null "http://127.0.0.1:${AGENT_PORT}/ping" && break
  sleep 0.5
done
echo "Agent running (log: /tmp/live-assist-agent.log)."

"$PY" -m api.local --port "$CONSOLE_PORT"
