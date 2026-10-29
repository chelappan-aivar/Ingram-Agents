#!/usr/bin/env bash
# Deploy the Pipeline Runs page into the live dashboard (S3 + CloudFront).
#
#   scripts/deploy.sh            test, upload /pipeline, add the sidebar link, update CSP, invalidate
#   scripts/deploy.sh --dry-run  show what would be uploaded
#
# Only adds objects under pipeline/, one <script> tag in each page, and hosts to the CSP connect-src.
# Roll back with scripts/rollback.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
BUCKET="${SITE_BUCKET:-ingram-agents-dev-app-edgesitebucket3cccc238-ihhyzfnzepgv}"
DISTRIBUTION="${DISTRIBUTION_ID:-ENNIIXWOU2B8O}"
DRY=""
[[ "${1:-}" == "--dry-run" ]] && DRY="--dryrun"

echo "Account: $(aws sts get-caller-identity --query Account --output text)"

echo "== tests"
(cd "$HERE/pipeline-app" && node --test tests/)

echo "== upload pipeline/"
aws s3 sync "$HERE/pipeline-app/" "s3://$BUCKET/pipeline/" $DRY \
  --exclude "tests/*" --exclude "package.json" --exclude ".*" \
  --delete --cache-control "no-cache" --sse AES256 --only-show-errors
if [[ -z "$DRY" ]]; then
  echo "== sidebar link"
  python3 "$HERE/scripts/patch_nav.py"
  echo "== CSP"
  python3 "$HERE/scripts/update_csp.py"
  echo "== invalidate CloudFront"
  aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION" --paths "/*" --query "Invalidation.Id" --output text
fi
echo "Done. https://d257wmhppqdfnv.cloudfront.net/pipeline/"
