#!/usr/bin/env bash
set -euo pipefail

# Configure the "main protection" repository ruleset. Existing classic branch
# protection is separate and this script leaves it unchanged. Applying needs
# admin access to the repo (the kollaborai account). `--check` is read-only and
# exits 1 on drift or hidden data. GitHub returns bypass_actors only to users
# with ruleset write access.
# Both check and apply require the exact all-path CODEOWNERS policy on main, so
# applying this ruleset before that file lands cannot strand the repository.
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
  ".github/CODEOWNERS"
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

expected_codeowners=$'* @maarco @kollaborai\n/.github/CODEOWNERS @maarco @kollaborai'
local_codeowners="$(cat .github/CODEOWNERS)"
if [[ "$local_codeowners" != "$expected_codeowners" ]]; then
  echo "Local .github/CODEOWNERS must cover every path and itself with @maarco and @kollaborai."
  exit 1
fi

if ! main_codeowners_response="$(gh api "repos/$repo/contents/.github/CODEOWNERS?ref=main" 2>/dev/null)" || \
   ! main_codeowners="$(jq -er 'select(.encoding == "base64" and (.content | type == "string")) | .content | @base64d' <<<"$main_codeowners_response" 2>/dev/null)"; then
  echo "Cannot verify .github/CODEOWNERS on main; land the owner file before checking or applying this ruleset."
  exit 1
fi
if [[ "$main_codeowners" != "$expected_codeowners" ]]; then
  echo "The .github/CODEOWNERS file on main must cover every path and itself with @maarco and @kollaborai."
  exit 1
fi
for owner in maarco kollaborai; do
  if ! permission="$(gh api "repos/$repo/collaborators/$owner/permission" --jq '.permission' 2>/dev/null)"; then
    echo "Cannot verify write access for CODEOWNERS account @$owner; refusing to check or apply the owner-review rule."
    exit 1
  fi
  case "$permission" in
    write|maintain|admin) ;;
    *)
      echo "CODEOWNERS account @$owner needs repository write access (reported: ${permission:-unknown})."
      exit 1
      ;;
  esac
done

# integration_id 15368 is GitHub Actions: no other app can satisfy a check.
# Include bypass_actors explicitly in the API payload, then read the ruleset
# back after applying it. `--check` also verifies that the visible list is empty.
expected_bypass='[]'
ruleset='{
  "name": "main protection",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 1,
      "dismiss_stale_reviews_on_push": true,
      "required_reviewers": [],
      "require_code_owner_review": true,
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
  live_ruleset="$(gh api "repos/$repo/rulesets/$id")"
  if ! jq -e 'has("bypass_actors") and (.bypass_actors | type == "array")' <<<"$live_ruleset" >/dev/null; then
    echo "Cannot verify bypass_actors on $repo; GitHub discloses this field only to users with ruleset write access."
    exit 1
  fi
  live="$(jq -S '{name, target, enforcement, bypass_actors, conditions, rules}' <<<"$live_ruleset")"
  expected="$(jq -S --argjson bypass "$expected_bypass" '. + {bypass_actors: $bypass}' <<<"$ruleset")"
  merge_ok="$(gh api "repos/$repo" --jq '.allow_merge_commit')"
  if diff <(echo "$expected") <(echo "$live") && [[ "$merge_ok" == "true" ]]; then
    echo "Live ruleset (one code-owner approval, strict checks, empty bypass list) and merge settings match this script."
    exit 0
  fi
  echo "Drift on $repo (< this script, > live; merge commits allowed: $merge_ok)."
  exit 1
fi

echo "Configuring ruleset \"main protection\" for $repo"

if [[ -n "$id" ]]; then
  gh api "repos/$repo/rulesets/$id" --method PUT --input - <<<"$ruleset" >/dev/null
else
  created_ruleset="$(gh api "repos/$repo/rulesets" --method POST --input - <<<"$ruleset")"
  if ! id="$(jq -er '.id | select(type == "number")' <<<"$created_ruleset" 2>/dev/null)"; then
    echo "Created the main protection ruleset but could not identify it for readback verification."
    exit 1
  fi
fi

if [[ -z "$id" ]]; then
  echo "Could not identify the main protection ruleset for readback verification."
  exit 1
fi

if ! applied_ruleset="$(gh api "repos/$repo/rulesets/$id" 2>/dev/null)"; then
  echo "Could not read back the main protection ruleset after applying it."
  exit 1
fi
if ! jq -e 'has("bypass_actors") and (.bypass_actors | type == "array")' <<<"$applied_ruleset" >/dev/null; then
  echo "Cannot verify bypass_actors after apply; GitHub discloses this field only to users with ruleset write access."
  exit 1
fi
applied="$(jq -S '{name, target, enforcement, bypass_actors, conditions, rules}' <<<"$applied_ruleset")"
expected="$(jq -S '{name, target, enforcement, bypass_actors, conditions, rules}' <<<"$ruleset")"
if ! diff <(echo "$expected") <(echo "$applied"); then
  echo "Applied ruleset does not match the requested protection policy on $repo; refusing success."
  exit 1
fi

# Automatic arming is disabled. Leave the separate auto-merge setting untouched.
gh api "repos/$repo" --method PATCH -F allow_merge_commit=true >/dev/null

echo "Ruleset applied. One code-owner approval and required checks: ci/tests, ci/standards-check, ci/security-scan"
