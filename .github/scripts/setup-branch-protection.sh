#!/usr/bin/env bash
set -euo pipefail

# Configure the "main protection" ruleset that guards main (classic branch
# protection is not used). Applying needs admin access to the repo (the
# kollaborai account). `--check` needs read access only: it diffs the live
# ruleset against this one and exits 1 on drift.
#
# Required checks are job names in .github/workflows/ci.yml. Merge commits
# only: after a squash or rebase merge, `git pull --ff-only origin main` in
# the shared checkout refuses (AGENTS.md).

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

# integration_id 15368 is GitHub Actions: no other app can satisfy a check.
# No bypass_actors: an update leaves the live list alone, a new ruleset has none.
ruleset='{
  "name": "main protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 0,
      "dismiss_stale_reviews_on_push": true,
      "required_reviewers": [],
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": true,
      "require_extra_approval_for_unattributed_changes": true,
      "allowed_merge_methods": ["merge"]
    }},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": true,
      "do_not_enforce_on_create": false,
      "required_status_checks": [
        {"context": "ci/tests", "integration_id": 15368},
        {"context": "ci/standards-check", "integration_id": 15368},
        {"context": "ci/security-scan", "integration_id": 15368}
      ]
    }}
  ]
}'

id="$(gh api "repos/$repo/rulesets" --jq '.[] | select(.name == "main protection") | .id')"

if [[ "${1:-}" == "--check" ]]; then
  if [[ -z "$id" ]]; then
    echo "No \"main protection\" ruleset on $repo."
    exit 1
  fi
  live="$(gh api "repos/$repo/rulesets/$id" | jq -S '{name, target, enforcement, conditions, rules}')"
  merge_ok="$(gh api "repos/$repo" --jq '.allow_auto_merge and .allow_merge_commit')"
  if diff <(jq -S . <<<"$ruleset") <(echo "$live") && [[ "$merge_ok" == "true" ]]; then
    echo "Live ruleset and merge settings match this script."
    exit 0
  fi
  echo "Drift on $repo (< this script, > live; auto-merge and merge commits allowed: $merge_ok)."
  exit 1
fi

echo "Configuring ruleset \"main protection\" for $repo"

if [[ -n "$id" ]]; then
  gh api "repos/$repo/rulesets/$id" --method PUT --input - <<<"$ruleset" >/dev/null
else
  gh api "repos/$repo/rulesets" --method POST --input - <<<"$ruleset" >/dev/null
fi

# auto-merge.yml arms GitHub auto-merge, which the repo has to allow.
gh api "repos/$repo" --method PATCH -F allow_auto_merge=true -F allow_merge_commit=true >/dev/null

echo "Ruleset applied. Required checks: ci/tests, ci/standards-check, ci/security-scan"
