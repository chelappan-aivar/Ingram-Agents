#!/usr/bin/env bash
# Build the AgentCore Runtime code zip: agent code + Linux arm64 / Python 3.12 wheels.
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
cp -R "$ROOT/agent/intake" "$STAGE/"
cp -R "$ROOT/agent/funding" "$STAGE/"
cp "$ROOT"/shared/python/*.py "$STAGE/"    # opportunity form + Dynamics client (also the Lambda layer)
find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} +

(cd "$STAGE" && zip -qr -X "$BUILD/agent.zip" .)
echo "$BUILD/agent.zip"
