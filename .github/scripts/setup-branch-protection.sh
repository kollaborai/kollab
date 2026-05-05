#!/usr/bin/env bash
set -euo pipefail

# Configure main-branch protection for the public repository.
# Requires GitHub CLI authentication with admin access to the repo.

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI is required. Install it with: brew install gh"
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI is not authenticated. Run: gh auth login"
  exit 1
fi

repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
if [[ -z "$repo" ]]; then
  echo "Could not determine GitHub repository."
  exit 1
fi

required_files=(
  ".github/workflows/ci.yml"
  ".github/pull_request_template.md"
  ".github/ISSUE_TEMPLATE/bug_report.yml"
  ".github/ISSUE_TEMPLATE/feature_request.yml"
  "CONTRIBUTING.md"
  "SECURITY.md"
  "SUPPORT.md"
  "docs/release-process.md"
)

for file in "${required_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "Missing required repo-governance file: $file"
    exit 1
  fi
done

echo "Configuring branch protection for $repo:main"

gh api "repos/$repo/branches/main/protection" \
  --method PUT \
  --field required_status_checks='{"strict":true,"contexts":["ci/tests","ci/standards-check","ci/security-scan"]}' \
  --field enforce_admins=true \
  --field required_pull_request_reviews='{"dismiss_stale_reviews":true,"require_code_owner_reviews":false,"required_approving_review_count":1,"require_last_push_approval":true}' \
  --field restrictions=null \
  --field required_linear_history=true \
  --field allow_force_pushes=false \
  --field allow_deletions=false \
  --field required_conversation_resolution=true

echo "Branch protection configured. Required checks: ci/tests, ci/standards-check, ci/security-scan"
