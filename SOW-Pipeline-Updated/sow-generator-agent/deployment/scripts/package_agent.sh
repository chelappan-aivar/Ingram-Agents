#!/usr/bin/env bash
# Build the AgentCore Runtime code zip: agent code (with its prompts, templates, policies and logo) +
# Linux arm64 / Python 3.12 wheels.
# Output: build/agent.zip (path printed on stdout)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
STAGE="$BUILD/agent"

rm -rf "$STAGE" "$BUILD/agent.zip"
mkdir -p "$STAGE"

# AgentCore Runtime runs on Linux arm64; only accept prebuilt wheels for that platform.
uv pip install --quiet \
  --target "$STAGE" \
  --python-version 3.12 \
  --python-platform aarch64-manylinux2014 \
  --only-binary=:all: \
  -r "$ROOT/agent/requirements.txt" >&2

cp "$ROOT/agent/main.py" "$STAGE/"
# sow_generator/resources/ holds package data read via importlib.resources: it must ship inside the zip.
cp -R "$ROOT/agent/sow_generator" "$STAGE/"
find "$STAGE" \( -name "__pycache__" -o -name ".pytest_cache" \) -type d -prune -exec rm -rf {} +
find "$STAGE" -name ".DS_Store" -delete
for f in prompts/system_prompt.md templates/aiml/ai-ml.md policies/sow_compliance_rules.yaml assets/ingram-micro-logo.png; do
  [[ -f "$STAGE/sow_generator/resources/$f" ]] || { echo "missing resource $f" >&2; exit 1; }
done

(cd "$STAGE" && zip -qr -X "$BUILD/agent.zip" .)
echo "$BUILD/agent.zip"
