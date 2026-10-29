#!/usr/bin/env bash
# Build the AgentCore Runtime code zip: agent code + Linux arm64 / Python 3.12 wheels.
# Output: build/agent.zip (path printed on stdout). AgentCore limits: 250 MB zipped, 750 MB unzipped.
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

# Our code at the zip root: the runtime starts main.py; schema/ must sit next to discovery_agent/ (analysis/validate.py).
cp "$ROOT/agent/main.py" "$STAGE/"
cp -R "$ROOT/agent/discovery_agent" "$ROOT/agent/schema" "$STAGE/"
find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "$STAGE" -type d -exec chmod 755 {} +   # AgentCore needs 755 on directories, 644 on files
find "$STAGE" -type f -exec chmod 644 {} +

(cd "$STAGE" && zip -qr -X "$BUILD/agent.zip" .)

listing="$(unzip -l "$BUILD/agent.zip")"   # not piped into grep -q: with pipefail that can report a false failure
for f in main.py schema/report.schema.json discovery_agent/prompts/extraction.md bedrock_agentcore/__init__.py; do
  grep -q " $f\$" <<<"$listing" || { echo "$f missing from the zip" >&2; exit 1; }
done
bad="$(find "$STAGE" -name "*.so*" -type f -exec file -b {} \; | grep -vc "ARM aarch64" || true)"
[[ "$bad" == "0" ]] || { echo "non-arm64 native library in the package ($bad)" >&2; exit 1; }
echo "agent.zip: $(du -m "$BUILD/agent.zip" | cut -f1) MB zipped, $(du -sm "$STAGE" | cut -f1) MB unzipped" >&2
echo "$BUILD/agent.zip"
