#!/usr/bin/env bash
# Put the latest UI of the six agents into the portal (https://d257wmhppqdfnv.cloudfront.net/agents/<agent>/).
#
#   apps/<id>/        each agent's own UI (from the Amplify sources), pointed at its live API
#   agents/<slug>/    portal page that hosts it in a same-origin frame (replaces the older built-in page)
#
# Rollback: scripts/rollback.sh restores the headers policy and removes /pipeline; the original agent pages are in
# _backup_live_site/ (aws s3 sync _backup_live_site/agents s3://$BUCKET/agents).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
BUCKET="${SITE_BUCKET:-ingram-agents-dev-app-edgesitebucket3cccc238-ihhyzfnzepgv}"
DISTRIBUTION="${DISTRIBUTION_ID:-ENNIIXWOU2B8O}"

python3 "$HERE/scripts/build_apps.py"

echo "== upload apps/"
aws s3 sync "$HERE/site-apps/apps/" "s3://$BUCKET/apps/" --delete --cache-control "no-cache" --sse AES256 --only-show-errors

echo "== agent pages"
for shell in "$HERE"/site-apps/shells/*.html; do
  slug="$(basename "$shell" .html)"
  for key in "agents/$slug/index.html" "agents/$slug/run/index.html"; do
    aws s3api put-object --bucket "$BUCKET" --key "$key" --body "$shell" --content-type "text/html; charset=utf-8" \
      --cache-control "no-cache" --server-side-encryption AES256 >/dev/null
  done
  # The portal's client-side router would fetch these data files and show the old built-in page. Without them it
  # falls back to a normal page load, which serves the page above.
  aws s3 rm "s3://$BUCKET/agents/$slug/" --recursive --exclude "*.html" --only-show-errors
  echo "  $slug"
done

echo "== headers policy (same-origin frames)"
python3 "$HERE/scripts/update_csp.py"
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION" --paths "/*" --query "Invalidation.Id" --output text
