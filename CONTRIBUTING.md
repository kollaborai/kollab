# Contributing to Kollab

Thanks for your interest in improving Kollab. This guide explains the expected
workflow for issues, pull requests, local setup, validation, and release-safe
changes.

## Before You Start

- Search existing issues and pull requests before opening a duplicate.
- Open an issue for non-trivial bugs, features, public behavior changes, or
  release/process work.
- Do not include API keys, OAuth tokens, raw cookies, private conversation logs,
  local runtime state, or customer data in issues or pull requests.
- Keep changes focused. Separate docs, runtime, tests, packaging, and cleanup
  work when possible.

## Development Setup

```bash
git clone https://github.com/kollaborai/kollab.git
cd kollab
uv sync --all-packages --extra dev
uv run python main.py
```

If you are not using `uv`, install the project in editable mode:

```bash
python -m pip install -e ".[dev]"
python main.py
```

## Repository Boundaries

Kollab is a monorepo. Put changes in the layer that owns the behavior:

- `kollabor/`: app wiring, startup orchestration, CLI integration
- `packages/kollabor-ai`: providers, profiles, OAuth, prompt rendering
- `packages/kollabor-agent`: tools, MCP, permissions, agent loading
- `packages/kollabor-tui`: terminal UI, rendering, widgets, fullscreen flows
- `packages/kollabor-events`: event bus and hook abstractions
- `packages/kollabor-config`: configuration loading and utilities
- `packages/kollabor-plugins`: plugin framework and SDK
- `plugins/`: concrete plugin features
- `bundles/agents/`: bundled agent definitions and prompt assets
- `docs/`: public documentation, architecture records, release process

Prefer existing hook, plugin, command, widget, and manager patterns before adding
new wiring.

## Branch and Pull Request Workflow

In the Kollab maintainer workflow, every active writer owns one isolated
worktree, branch, and file scope. Keep one writer per path at a time. Read-only
reviewers may share a checkout. Contributors using a standalone clone may use a
regular local branch when no other writer shares that checkout.

1. Inspect repository status and existing worktrees; preserve all existing
   dirty files, branches, and worktrees. Claim a unique worktree path, branch,
   and file scope before editing.
2. For a new maintainer lane, fetch the intended base and create a new worktree
   from current `origin/main` after confirming that path and branch are unused:

   ```bash
   git fetch origin main
   git worktree add -b issue-123-fix ../kollab-wt/issue-123-fix origin/main
   ```

   Use a descriptive `docs/<slug>` branch when no issue tracks the work.
3. Make a focused change and run targeted validation from the owning worktree.
4. Update docs when behavior, setup, commands, config, or public APIs change.
5. Push normally and open a DRAFT pull request against `main`. Keep review and
   CI tied to the exact head; merge current `main` into the branch when needed.
   Never rebase or force-push.
6. Fill out the PR template, including commands run and rollout risk. Use
   Conventional Commit messages and leave the PR as a draft until an authorized
   reviewer marks it ready.
7. A designated integrator sequences authorized merge commits. The integration
   checkout remains source-read-only and may only fast-forward after a PR has
   already merged. Do not automatically remove or delete worktrees or branches.

## Validation

Run the smallest useful validation for your change. For the current public CI
baseline:

```bash
uv run python -m pytest \
  tests/unit/test_hub_project_scope.py \
  tests/unit/test_provider_models.py \
  tests/unit/test_provider_security.py \
  tests/unit/test_gemini_provider.py
uv run python -m ruff check --select E9,F63,F7,F82 kollabor packages plugins tests
uv run python -m py_compile kollabor/cli.py kollabor_cli_main.py plugins/hub/plugin.py
```

Additional useful checks:

```bash
uv run python -m pytest tests/
uv run python -m black kollabor packages plugins tests main.py
uv run python -m ruff check kollabor packages plugins tests
uv run python -m mypy kollabor plugins
```

For user-visible CLI, TUI, or runtime behavior, run the tmux/runtime smoke path
and prefer JSONL evidence over terminal pane text. See [tests/tmux/README.md](tests/tmux/README.md).

For engine changes, verify the service starts:

```bash
uv run python -m kollabor_engine serve --port 7433
curl -sf http://127.0.0.1:7433/health
curl -sf http://127.0.0.1:7433/ready
```

## Security and Public Release Hygiene

Before publishing release-facing changes, check for secrets and local artifacts:

```bash
gitleaks detect --source . --no-git -v
gitleaks detect --source . -v
trufflehog git file://$PWD --only-verified
```

Do not commit:

- `.env` files or real credentials
- local `~/.kollab` runtime data
- raw transcripts or private conversation logs
- local databases, generated logs, caches, or scratch files
- personal machine paths unless they are clearly generic examples

## Documentation

Update documentation in the same pull request when a change affects install,
configuration, command behavior, plugin APIs, provider behavior, security posture,
release process, or user-visible workflows.

Useful starting points:

- [README.md](README.md)
- [docs/README.md](docs/README.md)
- [docs/architecture/README.md](docs/architecture/README.md)
- [docs/release-process.md](docs/release-process.md)
- [AGENTS.md](AGENTS.md)

## Reporting Bugs

Open a GitHub issue with:

- the Kollab version
- operating system, shell, terminal emulator, and Python version
- exact commands or workflow steps
- expected behavior
- actual behavior
- redacted logs or screenshots when useful

For vulnerabilities, follow [SECURITY.md](SECURITY.md) instead of opening a
public issue with sensitive details.
