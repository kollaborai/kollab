# Software Development Lifecycle

This document is the standard for how work moves from idea to merged code in
this repository. The short version: trunk-based development, one isolated
worktree and branch per active writer, one named integrator, draft PRs by
default, and releases tagged only when Marco explicitly decides.

## Standard

We follow Trunk-Based Development: `main` is the single source of truth for
merged work. Each active writing lane uses its own worktree and branch based on
the latest `origin/main`; source changes reach `main` through a reviewed PR.
Conventional Commits 1.0.0 apply to commit messages and SemVer 2.0.0 to
versions. Release mechanics live in `docs/release-process.md`; the release
manager playbook lives in `bundles/skills/release-manager/SKILL.md`. The rules
below are canonical for Kollab; shorter repo instructions and templates point
here for detail.

## Roles

- Marco (operator): decides what ships and may authorize a concrete integration
  plan or bounded PR batch, including a class order (see Main protection and
  merge authority). That authorization covers its work without a separate
  permission request for each merge step. Marco retains release and
  tag authority; release tags always need his explicit instruction.
- Designated integrator: one active named owner for the repository's merge
  queue. The integrator verifies each exact PR head, base, and required gates,
  and may mark ready and merge only work within Marco's authorized plan. A
  different AI agent must review every PR's exact final head
  and record the required GitHub approval with whichever account, `kollaborai`
  or `maarco`, is not the PR author. GitHub prohibits author self-approval.
  That review is clean-room and posted as a recorded PASS or FAIL verdict on a
  pinned head before the PR is merge-ready; see Pull request gates.
  Once the code-owner gate is enabled, that approval must also come from one
  of the two repository code owners. GitHub verifies the approving account,
  not which agent performed the review; keep independent reviewer identity and
  exact SHA in private maintainer audit evidence.
  Record reviewer identity and exact reviewed SHA in private maintainer audit
  evidence. Keep the GitHub approval plain, with no AI attribution or footer.
  Every merge is performed as `kollaborai` with a merge commit; no `--admin`
  or ruleset bypass is allowed.
  Scope outside the authorization needs a new decision.
  Record the current integrator, authorization, and checkpoint in a shared,
  discoverable project record. A transfer is effective only after verifiable
  stop evidence for the prior integrator is recorded and checked; then update
  the shared record with the
  new owner, effective time, and scope. A planned or conditional transfer does
  not grant write authority. If the record, stop evidence, or ownership is
  missing, conflicting, stale, or unreachable, stay read-only until resolved;
  elapsed time or a local lock does not prove exclusive ownership.
- Writers: investigate, implement, test, open DRAFT PRs, keep branches updated,
  and respond to review. Writers do not approve or merge their own unreviewed
  changes.
- Koordinator: routes work, tracks the task ledger, and verifies gates before
  reporting to the operator.

## Intake: issue to tracked task

Work must be a tracked task (via `/goal` or the work queue) before edits.
GitHub issues are the normal intake for code work; directly authorized
documentation or process maintenance may use its existing authorization as the
tracked task without a separate issue. Small code fixes still get an issue so
the PR branch and commit can reference it.

Before feature implementation starts, the feature issue links a spec in
`docs/specs/` or states that the implementation PR will add one from
`docs/specs/TEMPLATE.md`. A bug fix records the concise `Fix brief` shape from
`docs/specs/FIX_BRIEF.md` in the issue or PR body. The DoD check reports missing
specs or briefs in warning mode; maintainers can switch it to blocking after
reviewing the open work.

## Writer worktrees and main integration

- Each active writing lane has one named owner, one unique worktree, and one
  branch. Record the owner, worktree path, branch, and file scope before edits.
  Do not assign concurrent writers to the same paths; arrange a clear handoff
  before ownership changes.
- Read-only reviewers may inspect a shared checkout. They do not edit it.
- Preserve every existing worktree, branch, staged change, and dirty file unless
  its owner explicitly authorizes a specific change. Never automatically clean
  up or delete worktrees or branches.
- The local `main` worktree is the integration checkout. Keep its source
  files read-only: inspect them or fast-forward after changes have already
  merged to remote `main`. Fetching refs and registering a new worktree may be
  done from the repository context; neither changes source in local `main`.
  Never edit, stage, commit, or push source there, or switch, reset, stash,
  clean, or restore it.
- Before creating a writer lane, inspect status and all worktrees, fetch the
  intended base, and record the exact `origin/main` SHA. Confirm the requested
  worktree path, local branch, and remote branch are unused; do not reuse an
  existing path or branch.

From the integration checkout, a new lane can be created like this after the
path and branch absence checks:

```bash
git status --short --branch
git worktree list
git fetch origin main
git rev-parse origin/main
test ! -e "../kollab-wt/<task-slug>" && test ! -L "../kollab-wt/<task-slug>" || { echo "worktree path exists"; exit 1; }
! git show-ref --verify --quiet "refs/heads/issue-<N>-<slug>" || { echo "local branch exists"; exit 1; }
remote_branch=$(git ls-remote --heads origin "refs/heads/issue-<N>-<slug>") || { echo "remote branch check failed"; exit 1; }
test -z "$remote_branch" || { echo "remote branch exists"; exit 1; }
git worktree add -b "issue-<N>-<slug>" "../kollab-wt/<task-slug>" origin/main
```

Use `issue-<N>-<slug>` for issue work, `docs/<slug>` for documentation work
without an issue, or `codex/<scope>` for a Codex-authored lane without an issue.
Keep existing branch names unchanged. Check the new worktree's status
before editing. Do not copy uncommitted files from the integration checkout
into a lane without its owner's authorization.

From the owning worktree, commit only owned paths, set the PR branch upstream on
the first push, and create a DRAFT PR:

```bash
git status --short --branch
git commit -m "fix(cli): correct version display" -- kollabor/cli.py
git push -u origin HEAD:refs/heads/issue-8-version-display
gh pr create --draft --head issue-8-version-display --title "fix(cli): correct version display" --body "Fixes #8"
```

## Commits and PRs

- Make small, scoped commits from the owning worktree, naming only the paths
  owned by that lane. Use Conventional Commits and include the issue reference
  when one exists. Never broadly stage another writer's files.
- Add the PR's changelog entry as `changes/<issue>.<section>.md`
  (`changes/README.md`). Do not edit `CHANGELOG.md` or its packaged copy: only
  release (`release/...`) and changelog repair (`changelog/...`) branches may,
  and `ci/standards-check` refuses it elsewhere. Every PR writes its own file,
  so entries never conflict.
- Push the lane branch normally and open a DRAFT PR against `main`. Agents do
  not mark their own PR ready or merge it. Note overlapping PRs and path
  ownership so the integrator can sequence them.
- Independent review starts only while the PR head is current with `main`:
  `mergeStateStatus` is not BEHIND or DIRTY. Review must name the exact PR
  head SHA and the `main` base SHA it was reviewed against. Required CI
  checks must be green on that head, the branch must include the current
  base, and all review conversations must be resolved before merge. If the
  head moves after review, or `main` moves past the recorded base with
  conflicting or relevant changes, the verdict is void and the review is
  redone. A head that moved only by merging `main` keeps its verdict only when
  an independent reviewer posts an addendum pinned to the new head and base,
  confirming that the PR's own diff is unchanged apart from changelog lines and
  that nothing merged from `main` touches what the PR changes or relies on.
  Otherwise the review is redone.
- When `main` advances, update the writer branch by fetching and merging
  `origin/main` into it, then push normally. If the remote PR branch advanced,
  fetch and merge that branch too. Resolve conflicts only within owned paths;
  stop and coordinate if a conflict crosses another owner's scope. Never rebase
  or force-push; preserve history with ordinary merges.
- One active named integrator coordinates the repository's merge queue and lands PRs covered by
  Marco's authorized plan, using merge commits only. Marco authorizes the scope;
  the integrator executes it after the review and CI gates below, with no repeat
  permission request for each listed PR.
- After a PR has already merged, the integration checkout may run
  `git pull --ff-only origin main`. Verify its HEAD equals `origin/main`.
  If the pull refuses or the SHAs differ, stop and report the exact local,
  remote, and PR state. Do not reset, rebase, or force-push to hide divergence.

## Main protection and merge authority

`main` is protected. Marco authorizes concrete integration plans or bounded PR
batches; an authorization covers the listed merge steps without a repeated
permission request per PR. The designated integrator may mark a covered PR
ready and merge it with a merge commit after independent source review of its
exact final head, current base, required CI, and resolved review conversations.
A different AI agent must submit the required GitHub approval using whichever
account, `kollaborai` or `maarco`, is not the PR author. Record reviewer
identity and exact reviewed SHA in private maintainer audit evidence. Keep the
GitHub approval plain, with no AI attribution or footer. GitHub prohibits
author self-approval. Every merge is performed as `kollaborai` with a merge
commit and no admin or ruleset bypass. Work outside the authorized scope requires a new
decision. This authority does not cover release tags, which require Marco's
separate explicit instruction.

A class order counts as a bounded PR batch. A class order is an order in
Marco's own words that covers a class of PRs for a stated scope or time window,
for example "merge as many ready PRs as you can tonight". It needs no PR list:
it covers every PR in that class that passes the gates above. A reviewer can
still block a PR with findings, but no agent can narrow or cancel the order
itself. Send a doubt about its scope to Marco and keep merging the PRs it
clearly covers. An agent's paraphrase or an automated answer is not a class
order. A class order never covers release tags, and it covers a release PR only
if it says so.

## Code-owner approval and bootstrap

`.github/CODEOWNERS` assigns every path and the CODEOWNERS file itself to
`@maarco` and `@kollaborai`. With one required approval and code-owner review
enabled, either owner can satisfy the owner approval. After activation, a PR
from an external contributor needs approval from one of those accounts; a PR
authored as either account needs approval from the other because GitHub
prohibits author self-approval. The separately assigned AI review and its
exact-head identity record remain mandatory in private audit evidence; GitHub
enforces the required owner-account approval, not the identity or
independence of the AI reviewer.

Bootstrap this rule without locking `main`: first merge the PR that adds the
exact `.github/CODEOWNERS` file under the existing approval, strict CI,
conversation-resolution, and merge-only gates. Only after that file is present
on `main`, use the repository owner account `kollaborai` to run
`.github/scripts/setup-branch-protection.sh`. The script refuses to apply or
report success unless the remote `main` file has the expected all-path and
self-protecting owners and both accounts retain repository write access. Then
run `.github/scripts/setup-branch-protection.sh --check` as an identity with
ruleset write access and verify that `require_code_owner_review` is enabled,
one approval and the existing checks remain required, and bypass actors are
empty. Until that readback passes, treat code-owner enforcement as pending.

## Pull request gates

Writers ALWAYS open PRs as drafts and do not mark their own PRs ready. The
designated integrator marks ready only for covered work after independent
review and exact-head gates. Automatic arming remains disabled because the
ruleset cannot verify the independent AI review; the separate exact-head audit
remains required. Do not arm GitHub auto-merge manually.

The merge bot (`.github/workflows/auto-merge.yml`, #722) is a separate path and
is enabled in this repository. The repository variable `PR_GATES_AUTO_MERGE`
decides what it does: `dry-run` logs what it would merge, `on` also merges, and
unset turns it off. It merges a PR only when every gate holds on the current
head, including the commit status `review/independent`, which the approving
account posts after an independent review of that exact head. It leaves any PR
touching `.github/` to the integrator, who otherwise merges as described below.
Changing the variable is a repository owner's decision; see
`docs/operations/pr-gates-auto-merge.md`.

A PR must be current with `main`, have the required approval, all configured
checks green, and all review conversations resolved. After the bootstrap above,
the `main` ruleset requires one approving code-owner review, dismisses stale
approvals on push, does not require approval from someone other than the last
pusher, and enforces strict current-base status, resolved threads, and only the
`merge` method. Its required checks are `ci/tests`, `ci/standards-check`, and
`ci/security-scan`. The
`kollaborai` owner readback showed an empty bypass list and
`current_user_can_bypass=never`. Repository auto-merge is off. Run
`.github/scripts/setup-branch-protection.sh --check` with an identity that has
ruleset write access; GitHub discloses `bypass_actors` only at that access
level. The check remains read-only and fails closed if the field is hidden.
Recheck the live ruleset and repository settings before integration. No admin
or ruleset bypass is allowed.

Approvals are recorded by a different AI agent after reviewing the exact
final head. The agent uses whichever account, `kollaborai` or `maarco`, is not
the PR author. Record reviewer identity and exact reviewed SHA in private
maintainer audit evidence; keep the GitHub approval plain, with no AI
attribution or footer. GitHub prohibits authors from approving their own PRs;
do not attempt self-approval. A new push
dismisses stale approvals and requires fresh review and CI.

The review itself is clean-room. The reviewer has not seen the code before the
review: it is not the author and not any agent from the writer's lane. It
works in a scratch environment with no shared checkout and no shared
`~/.kollab` state (fresh clone or worktree, fresh venv), runs the tests
itself, and posts the verdict on the PR. That PR comment records the pinned
head SHA, the base SHA, the commands run, and PASS or FAIL. Reviewer identity
and model stay in private maintainer audit evidence; the GitHub approval
itself stays plain, with no AI attribution or footer. A PR is merge-ready
only with such a verdict on its current head. CI green alone is not
sufficient: CI plus an independent pinned-head review is the bar.

Every merge uses `kollaborai` and a merge commit. Never use `--admin` or a
ruleset bypass. GitHub's merge queue is not on yet (#857): GitHub offers it
only to organization-owned repos, and this repo belongs to the personal
account `kollaborai`. `ci.yml` already runs on `merge_group`, so once the repo
is in an organization the queue can be added to the `main protection` ruleset
(`.github/scripts/setup-branch-protection.sh`). Until then, a PR behind `main`
merges `main` into its branch before it lands. If identity, bypass status, current base, required checks, or
thread evidence is uncertain, stop. Before an authorized merge, recheck the
current head, base, checks, conversations, approval, and that auto-merge is not
armed. Then use a merge commit with a head match:

```bash
gh pr merge <PR-number> --repo kollaborai/kollab --merge --match-head-commit <reviewed-head-SHA>
```

The head match prevents merging a different head; it does not pin the base or
prove the review agent's identity. The strict current-base rule blocks stale
branches when enforced, but it does not replace an immediate base/check
recheck. Repository auto-merge is currently off; recheck live settings rather
than assuming they have not changed.
Desktop Mac/Linux builds
(the `binaries` and `desktop` workflows) run only on release tags and are not
merge requirements. `ci/binary-smoke` is an early Linux build, not a required
merge check.

## Implement and test

- Investigate first: read the code, its history, and the docs before changing
  anything.
- Respect package boundaries and follow existing patterns.
- Add or update tests for new behavior, including failure modes.
- Fix review findings red-first: land the failing regression test from the
  finding before the fix, per Review and merge.
- Run the targeted suite while iterating, then the full unit suite before
  opening the PR.
- Python 3.14 is the language floor and the launch runtime.

## Commits

Conventional Commits 1.0.0: `type(scope): summary`.

```bash
git commit -m "feat(webui): copy a profile and pin its reasoning effort"
```

- Types: feat, fix, docs, chore, refactor, test, ci, build, perf.
- Reference issues in the body: `Fixes #N`.
- Small, atomic commits. Imperative mood. Explain why when it is not obvious.

## Pull requests

- Agents ALWAYS open PRs as drafts: `gh pr create --draft`. Draft is the
  default for every agent-opened PR, no exceptions.
- Only the designated integrator marks a covered PR ready after independent
  review and exact-head gates, then merges manually with the head-match command
  above. Automatic arming is disabled.
- Merging needs the PR branch up to date with `main`. The writer updates its
  owned branch by fetching and merging `origin/main`, then pushing normally;
  preserve history with merge commits and never rebase or force-push.
- Note overlapping PRs and file ownership in the body so the integrator can
  sequence them.

## CI gates

Required checks: `ci/tests`, `ci/standards-check`, `ci/security-scan`. Merge
also requires the branch to be up to date with `main` and all PR
conversations resolved.

Desktop Mac/Linux builds (the `binaries` and `desktop` workflows) run only on
release tags. They are never PR checks and never merge requirements. The one
exception: `ci/binary-smoke` runs one quick Linux binary smoke build on every
PR and main push (about 49 seconds, no artifacts). It proves the build works
early; it is not a required merge check. CodeQL (`Analyze (...)`) also runs on
PRs and is not a required merge check.

The definition-of-done check (`.github/workflows/pr-gates-dod.yml`, #724) grades
each PR with one check run, `dod/ready-for-review`. It runs the base branch's copy
and no PR code, and it is not a required merge check. A failing grade still keeps
the merge bot from merging. `signed_commits` stays out of it until agent commits
are signed (#755).

The throughput report (`.github/scripts/pr_throughput_kpi.py`, #729) is opt-in and
runs by hand. It is not a check.

The stale PR sweeper (`.github/workflows/stale-pr-sweeper.yml`) runs daily and
on demand (`dry_run` reports without labeling). It lists open PRs with no new
commit and no review activity for 5+ days in one digest and adds the
`status/stale` label; `status/no-stale` opts a PR out. It only flags: it never
closes, comments on, or changes the draft state of a PR.

## Review and merge

Every PR requires an independent source review of the exact head; the PR author
cannot supply that review. Marco's explicit integration plan, batch, or class
order is the merge authorization for the work it covers. The designated
integrator owns queue
order, may mark covered PRs ready, and lands them with merge commits after the
review, base, CI, and thread-resolution gates pass. No extra permission request
is needed for a merge already covered by that plan. Out-of-scope work requires
a new authorization; release tags remain separately gated by Marco's explicit
instruction.

Review findings are fixed red-first. From each finding, write a regression
test that reproduces it and fails on the current head, land that test alone
(as its own commit, or as a strict `xfail` marker when the branch must stay
green), then write the fix against it. The test flips green with no edits to
the test itself; removing the strict `xfail` marker is the only permitted
test change. The independent reviewer runs that test as the concrete repro
instead of re-deriving one, and a fix commit that modifies its own regression
test is flagged in review. A finding no test can reproduce, such as wording in
a doc, says so in its fix commit instead.

## Release process

Full mechanics: `docs/release-process.md`. Summary:

- Author release source and version-preparation changes in a dedicated
  worktree/branch and merge them through the same reviewed PR gate.
- Build from the exact, clean, synchronized merged `main` checkout, never from
  an unmerged writer branch or scratch clone.
- Releases are tagged whenever Marco decides; agents may create a release tag
  ONLY when Marco explicitly instructs them to.
- Before tagging, check all machines for unpushed work and prove the integration
  checkout HEAD equals `origin/main` at the intended release merge commit.
- After the tag is pushed, dispatch `.github/workflows/publish.yml` from reviewed
  `main` with `-f tag=vX.Y.Z`. It builds from the tagged source and publishes the
  packages and the GitHub Release.

## Hotfixes

Same gates, shorter scope: issue, minimal fix and regression test in an isolated
writer worktree, DRAFT PR, exact-head CI/review, explicit merge authorization,
and merge commit by the designated integrator. Hotfix releases follow the
normal release process after the fix is merged and `main` is synchronized.

## Productization

Enhancements ship as opt-in, default off, repo-agnostic product surfaces (skill,
extension, agent, or plugin). The toggle defaults to off; repo, owner, check
names, identities, schedules, and paths come from config or provider discovery.
Hub job registration and ruleset or required-status changes need separate owner
sign-off. The checklist to copy into the PR body and the #711-#730 surface
mapping are in [docs/productization.md](docs/productization.md).

## Definition of done

- The PR body contains the complete product-ready checklist from `docs/product-ready-checklist.md`, with evidence for completed items and a specific reason for every N/A.
- Tracked task complete, tests green on the required CI checks.
- The PR is covered by Marco's authorized concrete plan, batch, or class order
  (or is individually authorized). The designated integrator independently reviews
  the exact head, verifies required CI, and merges it (or explicitly closes it).
- The designated integrator fast-forwarded local `main` after merge:
  `git pull --ff-only origin main`.
- Docs updated when behavior or process changed.
- The handoff records exact worktree path, branch, base SHA, local HEAD, remote
  branch SHA, PR number/state, reviewed head SHA, required CI state, and the
  integration checkout's local/remote main SHAs. Any mismatch or dirty path is
  named; never claim synchronization from labels alone.
