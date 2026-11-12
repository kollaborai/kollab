# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

Before maintaining Kollab, read `bundles/skills/kollab-development/SKILL.md`.
It defines the unreleased development policy, current-implementation workflow,
agent handoff requirements, and conditional relay operations guide.
Feature specs use `docs/specs/TEMPLATE.md`; bug fixes use `docs/specs/FIX_BRIEF.md`.

## Project Overview

**Kollab Interface** - Terminal-based LLM chat application where **everything has hooks**. Every action triggers customizable hooks that plugins can attach to for complete customization.

## Architecture

Monorepo with extracted packages:
- **Core Application** (`kollabor/application.py`): Main orchestrator
- **Event System** (`packages/kollabor-events/`): Event bus + hook system (`kollabor_events`)
- **LLM AI** (`packages/kollabor-ai/`): API, conversation, model routing (`kollabor_ai`)
- **Terminal UI** (`packages/kollabor-tui/`): Rendering, input, widgets (`kollabor_tui`)
- **Agent System** (`packages/kollabor-agent/`): Shell execution, agent runtime (`kollabor_agent`)
- **Configuration** (`packages/kollabor-config/`): Config management (`kollabor_config`)
- **Plugin System** (`packages/kollabor-plugins/`, `plugins/`): Discovery and loading (`kollabor_plugins`)
- **LLM Orchestration** (`kollabor/llm/`): Coordinator, permissions, streaming
- **Commands** (`kollabor/commands/`): Slash command registry and handlers

## Reference pages

This file holds the rules that apply to every change. The reference for each area lives in
`docs/reference/agent-guide/`. Read the page before you touch its area. When an area changes, update its page; a rule every
change must follow goes in this file.

| Before you touch | Read |
|---|---|
| a package's files, `/config`, panels, engine routes that take a folder, the repo layout | `docs/reference/agent-guide/components.md` |
| anything in `plugins/hub/`, hub messages, vaults, presence, the web UI's agent list, hub debugging | `docs/reference/agent-guide/hub.md` |
| a plugin, a hook, config hooks (`.kollab/hooks.json`), plugin config widgets | `docs/reference/agent-guide/plugins-and-hooks.md` |
| entry points, config files and save targets, `<trender>` system prompt tags | `docs/reference/agent-guide/configuration.md` |
| version numbers, cutting a release, release binaries, code that starts kollab again | `docs/reference/agent-guide/releases.md` |
| pipe mode, tool permissions and voice approvals, terminal colors, slash commands | `docs/reference/agent-guide/features-and-commands.md` |

### Traps the reference pages explain

These three live in the reference pages and are easy to break without reading them:

- Code that starts kollab again uses `kollabor.binary.kollab_argv()`, never `sys.executable -m kollabor_cli_main` (`releases.md`).
- `kollab --hub msg <name> <text>` reaches every online agent and wakes only the named one; send one message per agent you need to act (`hub.md`).
- An engine route that opens a local folder calls `folder_or_400` first and uses what it returns. The Code routes pass session-daemon paths to that daemon for validation (`components.md`).

## Terminal UI rules

### CRITICAL: Render State Management Rules

**NEVER directly manipulate these `terminal_renderer` properties:**
- `input_line_written`, `last_line_count`, `_last_render_content`, `writing_messages`

**ALWAYS use `MessageDisplayCoordinator` methods:**

```python
# Display messages
renderer.message_coordinator.display_message_sequence([...])

# Modal/fullscreen transitions
renderer.message_coordinator.enter_alternate_buffer()  # Before opening modal
renderer.message_coordinator.exit_alternate_buffer()  # After modal closes
renderer.message_coordinator.exit_alternate_buffer(restore_state=True)  # Restore state
```

**Why:** Direct manipulation causes duplicate input boxes, stale renders, and incorrect clearing. The coordinator uses flag-based coordination - `enter_alternate_buffer()` sets `writing_messages=True` to block the render loop.

**Modal exit patterns:**
```python
# Standard exit - restores state and renders input
await self._exit_modal_mode()

# Minimal exit - for commands displaying their own content
await self._exit_modal_mode_minimal()
```

**Safe direct calls:**
- `terminal_renderer.clear_active_area()` - uses state correctly
- `terminal_renderer.invalidate_render_cache()` - just clears cache

### Modal/Overlay Pattern for Status Widgets

**CRITICAL: Any fullscreen modal MUST use the coordinator pattern.**

```python
async def _show_my_modal(self) -> bool:
    self.coordinator.enter_alternate_buffer()
    try:
        sys.stdout.write('\033[?1049h')  # Enter alternate buffer
        sys.stdout.write('\033[?25l')    # Hide cursor
        sys.stdout.flush()
        # ... render and handle input ...
    finally:
        sys.stdout.write('\033[?25h')    # Show cursor
        sys.stdout.write('\033[?1049l')  # Exit alternate buffer
        sys.stdout.flush()
        self.coordinator.exit_alternate_buffer(restore_state=True)
    return result
```

**Why:** `coordinator.enter_alternate_buffer()` pauses the render loop, `exit_alternate_buffer()` resets render state. Without this, artifacts remain after modal closes. The ANSI alternate buffer preserves main screen content.

### STDOUT IS SACRED

**CRITICAL: Never use print() or sys.stdout.write() in plugin code.**

The terminal UI is a delicate render system. Any rogue stdout writes
corrupt the display (wrong cursor position, duplicate input boxes,
garbled status bar). There are exactly TWO legal ways to output:

**For messages visible to the user:**
```python
renderer.message_coordinator.display_message_sequence([
    ("system", "your message", {"display_type": "info"})
])
```

**For hub agent messages:**
```python
renderer.message_coordinator.display_message_sequence([
    ("agent", content, {"agent_color": color, "tag_char": " > "})
])
```

**For logging (goes to file, not screen):**
```python
logger.info("debug info here")
```

**NEVER do this in plugins:**
```python
print("anything")           # ILLEGAL - corrupts UI
sys.stdout.write("anything") # ILLEGAL - corrupts UI
sys.stderr.write("debug")   # Use logger.debug() instead
```

The only code allowed to call print() is `message_coordinator._output_rendered()`
which IS the render system's output path. Everything else must route through it.

### UI Design System (`kollabor_tui.design_system`)

**CRITICAL: Always use existing UI components. Never invent new patterns.**

**Core Imports:**
```python
from kollabor_tui.design_system import T, S, C, Box, TagBox, solid, solid_fg, gradient
```

**Theme Access (`T()`):**
- `T().primary`, `T().dark`, `T().text`, `T().text_dim`
- `T().success`, `T().error`, `T().warning`

**Style Constants (`S`):**
- `S.BOLD`, `S.RESET_BOLD`, `S.DIM`, `S.RESET_DIM`, `S.UNDERLINE`, `S.RESET_UNDERLINE`

**Box Rendering (solid block style):**
```python
solid_fg("▄" * width, T().dark[0])              # Top edge
solid(f"   {text:<{w}}", T().dark[0], T().text, w)  # Content
solid_fg("▀" * width, T().dark[0])              # Bottom edge
```

**TagBox (tag + content pattern):**
```python
TagBox.render(
    lines=["content"],
    tag_bg=T().primary[0], tag_fg=T().text_dark, tag_width=3,
    content_colors=T().dark[0], content_fg=T().text,
    content_width=width - 7,
    tag_chars=[" > "],
)
```

**Widgets (`kollabor_tui.widgets`):**
`CheckboxWidget`, `DropdownWidget`, `SliderWidget`, `TextInputWidget`, `LabelWidget`, `ProgressWidget`, `SpinBoxWidget`

**DO NOT:**
- Create box-drawing with characters (use solid block style instead)
- Hardcode colors (use `T()`)
- Access `terminal_state.width` directly (use `get_size()`)
- Invent new widget styles

### Universal Terminal State Management

**CRITICAL: All code must access terminal dimensions through the global state.**

```python
from kollabor_tui.terminal_state import get_terminal_size, get_terminal_width, get_terminal_height

width, height = get_terminal_size()
width = get_terminal_width()
height = get_terminal_height()

# Direct instance
from kollabor_tui.terminal_state import get_global_terminal_state
ts = get_global_terminal_state()
width, height = ts.get_size()
```

**WRONG:**
```python
import shutil
size = shutil.get_terminal_size()  # NO!
import os
size = os.get_terminal_size()  # NO!
```

**For Plugins:**
```python
async def render_frame(self, delta_time: float) -> bool:
    width, height = self.renderer.get_terminal_size()
```

## Development Commands

```bash
# Installation
pip install -e .              # Development mode
pip install -e ".[dev]"       # With dev dependencies
pip install kollab             # From PyPI

# Running
kollab                        # Installed command
python main.py                # Development mode
echo "query" | kollab -p      # Pipe mode
kollab "query" --timeout 5min # With timeout

# Testing
tests/tmux/lib/test_runner.sh tests/tmux/specs/mcp_memory_usage.json  # JSON test
tests/tmux/run_all_tests.sh    # All tmux tests
python tests/run_tests.py      # Unit tests
python scripts/validate_bundled_agent_skills.py  # Bundled Agent Skills layout (agentskills.io)
python -m unittest tests.unit.llm.test_llm_plugin  # Specific test

# Code quality
python -m black kollabor/ plugins/ tests/ main.py   # Format (88 chars)
python -m ruff check kollabor/ packages/ plugins/  # Lint (120 chars)
python -m mypy kollabor/ plugins/                  # Type check
python scripts/clean.py                        # Clean cache

# Building
python scripts/clean.py
python -m build
python scripts/build_binary.py   # single-file binary for this machine -> dist/bin/
python -m twine upload --repository testpypi dist/*
```

**Log locations:**
- `~/.kollab/projects/<encoded-path>/logs/kollab.log` (daily rotation)
- `~/.kollab/config.json` (global config)
- `~/.kollab/projects/<encoded-path>/conversations/` (JSONL)

## Agent Verification Requirements (MANDATORY)

**All agents implementing features MUST create JSON-based tmux verification tests.**

1. **Create JSON test spec** in `tests/tmux/specs/`:
```json
{
  "name": "feature-name",
  "description": "What this test verifies",
  "config": {
    "command": "python main.py",
    "app_init_sleep": 3,
    "show_captures": false
  },
  "steps": [
    { "action": "start_app" },
    { "action": "slash_command", "command": "mcp", "subcommand": "show" },
    { "action": "sleep", "seconds": 1 },
    { "action": "capture" },
    { "action": "assert_contains", "pattern": "MCP|Status" }
  ]
}
```

2. **Available actions:** `start_app`, `slash_command`, `type`, `send_keys`, `capture`, `assert_contains`, `assert_not_contains`, `wait_for`, `shell`, `escape`, `enter`, `arrow`, `control`, `sleep`, `section`
   - `send_keys` types **literal text** — use `escape` / `enter` / `arrow` for special keys
   - `slash_command` sends `/`, waits for the palette, types the command — prefer it over `type` + `enter`
   - prefer `wait_for` over `sleep` before an assertion, and pass `--no-daemon` in `config.command` so the spec can't attach to a developer's live daemon

3. **Run:** `tests/tmux/lib/test_runner.sh tests/tmux/specs/your-test.json`

**Never let a test touch the OS keyring.** On macOS every read/store of a
missing Keychain entry pops a system dialog, so a spec that boots the app with
an `api_key` in its config prompts on every run. The harness exports
`KOLLAB_NO_KEYRING=1` for the app and every `shell` step, and
`keyring_enabled()` (`kollabor_ai.providers.security`) is off automatically
under pytest. Any new harness must do the same.

**Never let a test touch the real `~/.kollab`.** The harness runs the app command
and every `shell` step with `HOME` set to a throwaway directory (seeded with
`application.setup_completed` and the connect-guide marker), so a spec cannot
attach to a live daemon, join the developer's hub, wake running agents or write
to their conversations and vaults. A spec without its own `HOME` also gets its
own `KOLLAB_PROJECT_ROOT`, because hub sockets live in
`/tmp/kollabor-hub/<project hash>/`, outside `HOME`. When the spec ends the
harness stops every process whose environment carries that `HOME` and removes
the directory. A spec that sets `HOME` in its own command keeps it. Opt-outs in
`config`: `"real_home": true` (loud warning; only for a spec that needs the
developer's real providers) and `"first_run": true` (unseeded `HOME`, for the
first-run wizard). Any new harness must isolate `HOME` the same way.

4. **Report:**
```
implementation complete:
  feature: [name]
  test: tests/tmux/specs/feature-name.json
  result: PASS (X/Y assertions verified)
```

**DO NOT report implementation as complete without a passing test.**

See `tests/tmux/README.md` for complete documentation.

## Development Guidelines

### Git Workflow

**Pre-commit:** Commits to `kollabor/`, `plugins/`, and `tests/` must reference a GitHub issue.

**Changelog:** a PR adds its entry as `changes/<issue>.<section>.md` (`changes/README.md`) and never edits `CHANGELOG.md` or `kollabor/updates/CHANGELOG.md`; `ci/standards-check` refuses that outside `release/` and `changelog/` branches. The release PR folds the entries in with `python scripts/changelog.py build` (#855).

For Kollab, follow the canonical isolated-writer process in
`SOFTWARE_DEVELOPMENT_LIFECYCLE.md`. Each active writer claims one unique
worktree, branch, and file scope based on freshly fetched `origin/main`;
read-only reviewers may share a checkout. Keep the local `main` integration
worktree's source files read-only; inspect them or fast-forward after merged
PRs. Fetching refs and registering a new worktree may use Git metadata without
changing those files. Do not edit, stage, commit, or push source there, and
preserve existing dirty files and worktrees.

Use scoped Conventional Commits and DRAFT PRs. Marco authorizes a concrete
integration plan or bounded batch (a class order in his own words, such as
"merge as many ready PRs as you can tonight", counts; see
`SOFTWARE_DEVELOPMENT_LIFECYCLE.md`); its named integrator may mark covered PRs
ready and merge them after independent source review of the exact head, current
base, required CI, and resolved conversations. Do not repeat permission for
listed steps. A different AI agent must review every PR's exact final head and
record the GitHub approval as whichever account, `kollaborai` or `maarco`, is
not the PR author. GitHub prohibits author self-approval. Record reviewer
identity and exact reviewed SHA in private maintainer audit evidence; keep the
GitHub approval plain, with no AI attribution or footer. A new head requires
fresh review and CI. The review is clean-room and starts only on a current
base: the reviewer has not seen the code before the review (not the author,
not any agent in the writer's lane), works in a scratch environment, runs the
tests, and posts a PR verdict recording the pinned head SHA, base SHA,
commands run, and PASS or FAIL; review waits while the head is BEHIND or
DIRTY against `main`, and a moved head or a relevant base move voids the
verdict. A head moved only by merging `main` keeps its verdict only when an
independent reviewer posts an addendum pinned to the new head and base,
confirming the PR's own diff is unchanged apart from changelog lines and that
nothing merged from `main` touches what the PR changes or relies on. Review
findings are fixed red-first: land the failing regression test (alone, or
marked strict `xfail`) before the fix, and the fix flips it green without
editing the test; a finding no test can reproduce, such as wording in a doc,
says so in its fix commit instead. Every merge uses
`kollaborai` and a merge commit; never use `--admin` or a ruleset bypass.
GitHub's merge queue is not on yet (#857): GitHub offers it only to repos
owned by an organization, and this repo belongs to the personal account
`kollaborai`. Until it moves, a PR behind `main` merges `main` into its branch
before it lands. `ci.yml` already runs on `merge_group`, so the queue can be
turned on as soon as the repo is in an organization.
After code-owner enforcement is enabled, `.github/CODEOWNERS` covers every
path and protects itself; one of `@maarco` or `@kollaborai` must approve. The
other account approves PRs authored under either account. GitHub enforces the
approving account, not independent AI-review identity; retain that exact-head
evidence privately and follow the bootstrap sequence in
`SOFTWARE_DEVELOPMENT_LIFECYCLE.md`.
Out-of-scope work needs new authorization, and release tags always need
separate explicit instruction. Update branches with ordinary merges; never
rebase or force-push. Report worktree, branch, base/head SHAs, remote/PR state,
and synchronized local/remote `main` SHAs. The `main` checkout's source files
are never edited, staged, or committed.

### Code Standards
- PEP 8 with Black formatting (88-char) and ruff linting (120-char)
- Lint: `ruff check` must pass with 0 violations before commit
- Double quotes for strings, single for chars
- Type hints on public functions
- `async def` for all hooks (even if no await)
- `logging.getLogger(__name__)` for logging
- File locks go through `kollabor_config.file_lock` (`locked_file`, `lock`, `try_lock`, `unlock`), never `fcntl` directly: it also works on Windows (#268)

### Async/Await Patterns
- Main event loop: `asyncio.run()` in `cli_main()`
- Concurrent tasks: `asyncio.gather()` for render loop + input handler
- Background tasks: Use `app.create_background_task()` for tracking
- Cleanup: All tasks cancelled in `app.cleanup()` via `finally`
- Plugin hooks: Must be async

### Testing Strategy
- **JSON tmux tests** (`tests/tmux/specs/`) - PRIMARY approach for UI/features
- Unit tests in `tests/unit/`
- Integration tests in `tests/integration/`
- Visual tests in `tests/visual/`
- Component tests (`test_*.py`)

### Hook Development
Consider:
- Hook priority (`HookPriority` enum: SYSTEM=1000, SECURITY=900, PREPROCESSING=500, LLM=100, POSTPROCESSING=50, DISPLAY=10)
- Error handling - hooks shouldn't crash (caught by HookExecutor)
- Performance - hooks are in hot path
- State management - avoid shared mutable state
- Return modified context from hooks
- All handlers must be async

## Completion Checklist

Before closing out any feature or fix, run this audit.
Use sub agents for parallel checks when possible.

**Robustness:**
- [ ] Identify 5 failure modes and implement fixes
- [ ] Are errors visible to the user? (message_coordinator display, not print)
- [ ] Did this remove or break existing functionality?
- [ ] Are there pre-existing errors in nearby code? Fix them too
- [ ] Does the render loop survive this change? (no stale writing_messages)

**Testing:**
- [ ] Tmux test spec created in `tests/tmux/specs/`?
- [ ] Unit tests in `tests/unit/` for new logic?
- [ ] `ruff check` passes with 0 violations?
- [ ] `py_compile` passes on all modified files?
- [ ] Existing test suite still green? (`python -m pytest tests/unit/ -x -q`)

**Documentation + Discoverability:**
- [ ] `CLAUDE.md` (a rule for every change) or its `docs/reference/agent-guide/` page (reference for one area) updated for new components/patterns?
- [ ] `README.md` updated if user-facing feature?
- [ ] `docs/` page created or updated? (features/, guides/, specs/)
- [ ] Related docs cross-linked?

**Hub / Agent System (if applicable):**
- [ ] Pipeline tag count updated in log line?
- [ ] Agent prompt docs updated? (`bundles/agents/_base/sections/`)
- [ ] Vault/crystal tags documented in spec?
- [ ] Hub slash commands table updated in README?

**Plugin System (if applicable):**
- [ ] `register_plugin_tag` + `register_plugin_handler` pattern followed?
- [ ] `ToolExecutionResult` uses correct kwargs (`tool_id`, `tool_type`, `success`, `output`)?
- [ ] Handler follows vault_write pattern (lazy import, try/except, stream logging)?

**Changelog + Comms:**
- [ ] Git commit with issue reference?
- [ ] Does this impact kollabor.ai website docs? (`~/dev/kollabor.ai`)
- [ ] Does this impact kowork (web app)?
