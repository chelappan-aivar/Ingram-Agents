#!/usr/bin/env bash
# Replace the framed agent pages (agents/<slug>/ with an <iframe> of /apps/<id>/) by native pages: each one opens
# its agent's tab on the Pipeline Runs page (/pipeline/?agent=<tab>), so there is no site inside a site.
#
#   scripts/deploy_agent_pages.sh            write the six pages (and /run/ variants), then invalidate CloudFront
#   scripts/deploy_agent_pages.sh --dry-run  list what would be written
#
# Nothing is deleted. Roll back by restoring agents/ from _backup_live_site/ (see scripts/rollback.sh).
set -euo pipefail
BUCKET="${SITE_BUCKET:-ingram-agents-dev-app-edgesitebucket3cccc238-ihhyzfnzepgv}"
DISTRIBUTION="${DISTRIBUTION_ID:-ENNIIXWOU2B8O}"
DRY="${1:-}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# slug:tab id used by the Pipeline Runs page
PAGES="intake-agent:intake live-assist-agent:liveAssist discovery-agent:discovery catalyst-sow-generator-agent:sow architecture-agent:architecture cost-calculator-agent:cost"
for pair in $PAGES; do
  slug="${pair%%:*}"; tab="${pair##*:}"; url="/pipeline/?agent=$tab"
  cat > "$TMP/$slug.html" <<HTML
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Opening agent · Ingram Micro Agents Dashboard</title>
<meta http-equiv="refresh" content="0; url=$url"><script>location.replace("$url");</script></head>
<body><p><a href="$url">Open this agent</a></p></body></html>
HTML
  for key in "agents/$slug/index.html" "agents/$slug/run/index.html"; do
    if [[ "$DRY" == "--dry-run" ]]; then echo "would write $key -> $url"; continue; fi
    aws s3api put-object --bucket "$BUCKET" --key "$key" --body "$TMP/$slug.html" --content-type "text/html; charset=utf-8" \
      --cache-control "no-cache" --server-side-encryption AES256 >/dev/null
    echo "wrote $key -> $url"
  done
done
[[ "$DRY" == "--dry-run" ]] || aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION" --paths "/agents/*" "/pipeline/*" --query "Invalidation.Id" --output text
