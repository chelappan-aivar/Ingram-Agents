#!/usr/bin/env bash
# Undo the Pipeline Runs deployment: remove pipeline/, remove the sidebar script tag, restore the CSP.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
BUCKET="${SITE_BUCKET:-ingram-agents-dev-app-edgesitebucket3cccc238-ihhyzfnzepgv}"
DISTRIBUTION="${DISTRIBUTION_ID:-ENNIIXWOU2B8O}"
POLICY_ID="${HEADERS_POLICY_ID:-d21ed535-54d5-4563-952b-05c2a2221eb5}"
SAVED="$HERE/_backup_infra/cf-headers-policy.json"
# (GET echoes unset blocks as {}, which UPDATE rejects, so empty blocks are dropped on restore.)

python3 "$HERE/scripts/patch_nav.py" --remove
aws s3 rm "s3://$BUCKET/pipeline/" --recursive --only-show-errors

if [[ -f "$SAVED" ]]; then
  ETAG="$(aws cloudfront get-response-headers-policy --id "$POLICY_ID" --query ETag --output text)"
  python3 - "$SAVED" <<'EOF'
import json, sys
cfg = json.load(open(sys.argv[1]))["ResponseHeadersPolicy"]["ResponseHeadersPolicyConfig"]
cfg["SecurityHeadersConfig"] = {k: v for k, v in cfg["SecurityHeadersConfig"].items() if v}
json.dump(cfg, open("/tmp/headers-policy-restore.json", "w"))
EOF
  aws cloudfront update-response-headers-policy --id "$POLICY_ID" --if-match "$ETAG" --response-headers-policy-config file:///tmp/headers-policy-restore.json >/dev/null
fi
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION" --paths "/*" --query "Invalidation.Id" --output text
echo "Rolled back. The SOW/Cost API CORS additions are left in place (harmless); see README to remove them."
