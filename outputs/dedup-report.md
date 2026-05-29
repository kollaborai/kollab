# Duplicate-Function Scour Report

_Generated 2026-05-28 via AST detector (`scripts/find_dupes.py`) + 46-agent triage workflow (`scripts/dedup_workflow.js`)._

## Summary

- **46** exact-body clusters detected (38 cross-file)
- **28** genuine copy-paste duplicates
- **2** auto-fixed (pure utility, no render/hub path, mechanical merge)
- **12** functions are `clean_renderer.py`/`message_renderer.py` forked twins — one extract-shared-module decision (see below)
- **4** conversations-twin functions — decide as a unit (see below)
- **9** intentional (framework overrides, same-file aliases) — left alone
- **9** coincidental matches (identical body, different contract) — left alone

Detection is mechanical (AST body-hash, docstrings/whitespace/var-names normalized) so it is deterministic and catches cross-file duplicates that subsystem-siloed reading would miss. Each cluster was then triaged by an agent that read the actual function bodies and call sites.

**The conservative auto-fix count is the pipeline working as designed**, not underdelivering: 14 real dupes live in the render hot path (CLAUDE.md forbids auto-edits there), and the rest carry import-cycle or design decisions reserved for a human. The report is the deliverable; the safe automated changes are two trivial path/encode helper merges.

---

## Auto-fixed (applied)

Pure utility, not in the render hot path or hub flow, provably value-preserving. Applied on the working tree and verified: `ruff` clean, `py_compile` clean, full unit suite shows zero new failures (set-diff against baseline).

### Cluster #29 — `_current_package_root, _current_source_root` ✔ fixed

  - `kollabor/updates/auto_update.py:33` `_current_package_root()`
  - `kollabor/updates/git_update.py:44` `_current_source_root()`

**Canonical home:** kollabor/updates/git_update.py:44

**Why safe:** Both functions are byte-identical (return Path(__file__).resolve().parents[2]) AND share the same intent: locate the source-checkout repo root for the kollab --update flow. The discriminator an AST pass cannot see: both files live in the SAME directory (kollabor/updates/), so parents[2] resolves to the identical repo root regardless of which __file__ is used -- the merge provably preserves the value (a parents[N] dupe across different-depth dirs would NOT be safe). Pure path utility, no UI side effects, not in tui render hot path or hub flow. Caller pattern is a smoking-gun mirror: auto_update.py:79 `root = (repo_root or _current_package_root()).resolve()` vs git_update.py:56 `repo = (repo_root or _current_source_root()).resolve()`.

**Call sites:** 1 internal caller each, both private (_ prefixed), no external references in repo-wide grep. _current_package_root: only kollabor/updates/auto_update.py:79. _current_source_root: only kollabor/updates/git_update.py:56. Canonical must be git_update.py because auto_update.py already does `from .git_update import run_source_update` (line 11) -- putting the home there avoids inverting the dependency / a circular import. Mechanical fix: add _current_source_root to the existing line-11 import, repoint line 79, delete _current_package_root.

**Applied:** deleted `_current_package_root` from `auto_update.py`, added `_current_source_root` to the existing `from .git_update import ...` line, repointed the one caller. Both helpers were byte-identical and both files live in `kollabor/updates/`, so `parents[2]` resolves to the same root.

### Cluster #31 — `_encode_path, _encode_project_path` ✔ fixed

  - `packages/kollabor-engine/src/kollabor_engine/hub_bridge.py:30` `_encode_project_path()`
  - `plugins/hub/project_scope.py:51` `_encode_path()`

**Canonical home:** packages/kollabor-config/src/kollabor_config/config_utils.py:106

**Why safe:** Both functions are byte-identical 1-line pass-throughs (`return encode_project_path(path)`) with an identical docstring ("Match the encoder used for ~/.kollab/projects/<encoded>/.") -- the matching docstring is the copy-paste tell, not coincidence. Same intent, pure path encoder, no UI/hub side effects so safe_util (project_scope.py living under plugins/hub/ does not make it hub_flow; hub_flow is message/spawn/continuation). Not autofixable: in hub_bridge.py the bare `encode_project_path` import (line 19) is used ONLY inside the dead wrapper (line 32), so deleting the wrapper orphans the import and ruff's 0-violation gate fails unless the import is also stripped -- that turns the fix into "delete func + remove import" rather than a mechanical repoint, so per "when in doubt -> false" it stays false for a 3-line, already-dead payoff.

**Call sites:** _encode_project_path (hub_bridge.py:30): ZERO callers -- dead code; the module already imports and could use encode_project_path directly, but only the dead wrapper consumes that import. _encode_path (project_scope.py:51): exactly ONE caller, same-file at project_scope.py:87 (resolve_presence_dir path); module also imports encode_project_path directly. Canonical encode_project_path already exists at config_utils.py:106 and is already imported by BOTH files, so consolidating creates no new cross-package dependency. Clean fix: delete dead _encode_project_path + its now-orphaned import in hub_bridge.py; repoint project_scope.py:87 to encode_project_path(resolve_project_root()) and delete _encode_path (its import stays used).

**Applied:** `_encode_project_path` in `hub_bridge.py` was dead (zero callers) — deleted it and dropped the now-unused `encode_project_path` from its import block. `_encode_path` in `project_scope.py` had one same-file caller (`resolve_project_id`) — repointed it to the already-imported `encode_project_path` and deleted the wrapper. No new cross-package dependency: both files already imported the canonical function.

---

## Renderer twins — one extract-shared-module decision (biggest finding)

`packages/kollabor-tui/.../clean_renderer.py` and `packages/kollabor-tui/.../message_renderer.py` share **12 clustered functions** (11 byte-identical, 1 cosmetically diverged: `_truncate_plain`) — an entire tool-detail rendering helper family was forked between the two renderers. A 13th family member, `_tool_badge_color`, also diverged cosmetically and so wasn't auto-clustered. This is **one systematic duplication, not 12 independent dupes**: one extract decision resolves all of them at once.

**A follow-up investigation (read-only agent) confirmed both renderers are LIVE** — `CleanRenderer` is the config default (`application.py:385`), `ModernMessageRenderer` is the coordinator constructor fallback (`message_coordinator.py:109`) plus a config option, and `SimpleRenderer` already imports 4 of these helpers FROM `clean_renderer`. So retiring a renderer is a product decision, out of scope. **Recommendation: extract the live helpers into a neutral `packages/kollabor-tui/src/kollabor_tui/_tool_detail.py`** that all three renderers import (canonical = clean's copies; it's the live default). Footprint is tiny: `re`, `shlex`, `Optional`, and `T`/`solid_fg` from `design_system`.

**Three dead copies to delete in the same pass** (0 call sites, 0 external importers — guaranteed safe): `_tool_symbol` in clean_renderer (`:236`), `_tool_symbol` in message_renderer (`:171`), and a vestigial `@staticmethod _truncate_plain` in `message_renderer.py:559` (all bare `_truncate_plain(...)` calls resolve to the module-level fn at `:186`, never the static method). 11/13 are byte-identical; `_truncate_plain` and `_tool_badge_color` differ only cosmetically (docstring word / `in {x}` vs `== x`).

**REPORT ONLY** — these files are the CLAUDE.md render hot path. The extract must be tmux-verified before/after (render a tool_call + tool_result + agent_message under clean, then under modern, diff captures → must be byte-identical), so it is a human/agent task with verification, not an auto-fix.

Functions in the pair:

- **#0 `_tool_badge`** — _real_dupe_
- **#1 `_clean_tool_detail`** — _real_dupe_
- **#2 `_tool_symbol`** — _real_dupe_
- **#6 `_tool_summary_color`** — _real_dupe_
- **#7 `_named_arg`** — _real_dupe_
- **#8 `_format_tool_summary`** — _real_dupe_
- **#10 `_truncate_plain`** (+1 intra-file copy) — _real_dupe_
- **#16 `_render_tool_badge`** — _real_dupe_
- **#17 `_agent_marker`** — _real_dupe_
- **#24 `_mix_color`** — _real_dupe_
- **#25 `_normalize_tool_label`** — _real_dupe_
- **#32 `_assistant_text_color`** — _real_dupe_

---

## Conversations twins — finish the altview migration (UI bug, not just a dupe)

`plugins/altview/conversations_altview.py` and `plugins/fullscreen/conversations_plugin.py` are a forked file pair. The AST pass flagged **four** matching functions and each per-cluster agent (seeing only its slice) reached a different verdict. This is ONE systematic duplication — and investigating it surfaced a live UI bug.

**Verified facts (each grep/line-checked, not taken on agent word):**
- **Fullscreen wins by registration order.** `application.py:438` initializes fullscreen commands BEFORE altview (`:441`). Fullscreen claims `conversations`/`convos`/`sessions` first, so `altview/command_integration.py:231` sees the command already registered and SKIPS. `ConversationsAltView` loads but no command routes to it.
- **So the OLD, flickery fullscreen renderer is what runs on `/conversations`** — the AltView stack pauses the main render loop on push (`altview/stack_manager.py:157` `scheduler.pause()`), which is the flicker-prevention the newer architecture exists to provide. The shadowed path means users get the flicker the migration was meant to kill.
- **The altview port is unfinished:** `conversations_altview.py:484` `get_resume_session()` has ZERO callers on the altview path (verified: def-only, no call in `plugins/altview/` or `kollabor/altview/`). Its only consumer is `fullscreen/command_integration.py:257`. So even if altview won the command today, selecting a session wouldn't resume — the glue was never wired.

**Recommendation: finish the migration forward, scoped to conversations.** AltView is the intended architecture (it pauses rendering to prevent flicker); the fix is NOT to delete it. Two parts, both required — neither works alone:
1. **Wire altview's resume glue** — consume `get_resume_session()` in the altview pop path, mirroring `fullscreen/command_integration.py:257-351` (read the selected session, emit `ADD_MESSAGE` to actually resume).
2. **Retire ONLY fullscreen's `conversations` registration** so altview wins. Do NOT flip the global `438`/`441` init order — that would change the winner for every fullscreen↔altview pair (matrix, example, login, mcp_wizard).
Why not the two delete-shortcuts: deleting *fullscreen* alone fails over to the unwired altview port → broken resume. Deleting *altview* alone locks in the flicker permanently. Render hot path → needs a tmux acceptance test (`/conversations` → altview opens → select → resume fires) before it lands.

Divergence note: of the 4, only `set_app` and `get_resume_session` are truly byte-identical; `_render_footer` and `_get_display_name` differ cosmetically (comments / line-wrapping) with identical logic. The AST pass normalized those away.

Functions in the pair:

- **#4 `_render_footer`** — per-cluster verdict: _real_dupe/render_path_
    - `plugins/altview/conversations_altview.py:359`
    - `plugins/fullscreen/conversations_plugin.py:375`
- **#5 `_get_display_name`** — per-cluster verdict: _real_dupe/safe_util_
    - `plugins/altview/conversations_altview.py:384`
    - `plugins/fullscreen/conversations_plugin.py:396`
- **#19 `set_app`** — per-cluster verdict: _real_dupe/risky_other_
    - `plugins/altview/conversations_altview.py:55`
    - `plugins/fullscreen/conversations_plugin.py:63`
- **#20 `get_resume_session`** — per-cluster verdict: _not_dupe/na_
    - `plugins/altview/conversations_altview.py:484`
    - `plugins/fullscreen/conversations_plugin.py:503`

---

## Real duplicates — REPORT ONLY (need human review / live verification)

### Render hot path (CLAUDE.md forbids auto-edits — behavioral changes need live tmux verification)

**#27 — `_strip_ansi`** (3 copies)
  - `packages/kollabor-tui/src/kollabor_tui/render_layout.py:86` `_strip_ansi()`
  - `packages/kollabor-tui/src/kollabor_tui/modals/modal_renderer.py:1167` `_strip_ansi()`
  - `packages/kollabor-tui/src/kollabor_tui/modals/modal_state_manager.py:82` `_strip_ansi()`

_All three bodies are byte-identical pure-utility ANSI strip: `return re.sub(r"\033\[[0-9;]*m", "", text)`, copy-pasted as a private method onto three classes for width calculation. A canonical module-level `strip_ansi` already exists at status/utils.py:24 with the identical body (layout_renderer.py already imports it). So it is a genuine dupe with an obvious home. But one member lives in render_layout.py, which the project CLAUDE.md / task brief explicitly lists as a RENDER HOT PATH file (report-only). Per the hard constraint, a cluster touching the hot path is report_only -> render_path, safe_to_autofix=false; behavioral changes there need live verification an auto-fixer cannot perform._

Proposed home: packages/kollabor-tui/src/kollabor_tui/status/utils.py:24 (existing module-level strip_ansi; the three _strip_ansi methods should be replaced with `from ..status.utils import strip_ansi` and call-site updates)

### Cross-package contract (merging creates/deepens a package dependency or risks an import cycle)

**#3 — `detect_provider_from_api_key`** (2 copies)
  - `packages/kollabor-config/src/kollabor_config/loader.py:40` `detect_provider_from_api_key()`
  - `packages/kollabor-ai/src/kollabor_ai/profile_validator.py:48` `detect_provider_from_api_key()`

_Byte-identical bodies AND the profile_validator.py docstring explicitly says "Provider Detection (extracted from kollabor.config.loader)" -- a confirmed copy-paste, so real_dupe. But this is NOT a safe_util auto-merge for two reasons. (1) Cross-package contract gap: kollabor-config/pyproject.toml declares only kollabor-events as a dep, NOT kollabor-ai, even though loader.py already imports kollabor_ai at runtime; consolidating deepens an undeclared cross-package coupling -- a human boundary call. (2) Direction-sensitivity makes it non-mechanical: the only cycle-safe consolidation is INTO the kollabor_ai copy (config imports ai, and loader.py:10 already top-level-imports kollabor_ai.prompt_renderer). The opposite direction (the docstring-named original, config.loader) would create a circular import -- kollabor_ai/__init__ -> profile_validator -> kollabor_config.loader -> kollabor_ai.prompt_renderer mid-init = partial-init ImportError. A mechanical fixer could easily pick the wrong (cycle-inducing) direction. Pure string-prefix util, no UI/render/hub side effects, but the boundary + cycle risk push this to human review._

Proposed home: packages/kollabor-ai/src/kollabor_ai/profile_validator.py:48 (already re-exported from kollabor_ai/__init__; only cycle-safe direction since config->ai import already exists). Cleanest neutral home if the human wants to avoid the cross-package-contract question entirely: move it to kollabor-events, which BOTH packages already declare as a dependency.

**#11 — `_agent_pid_alive, _pid_alive`** (2 copies)
  - `kollabor/cli.py:1288` `_pid_alive()`
  - `plugins/hub/plugin.py:7624` `_agent_pid_alive()`

_Both bodies are byte-identical pure pid-liveness probes (if not pid: return False; try os.kill(pid,0): return True; except (OSError,ProcessLookupError): return False) -- genuine copy-paste, no UI side effects, NOT hub routing/spawn logic so the function itself is utility-grade. But it is not a mechanical merge: the cli.py copy is a nested closure inside the async _handle_cli_hub function (line 1282 enclosing def) and cannot be imported as-is, and the two homes live in different layers (top-level kollabor/cli.py vs the plugins/hub plugin). Consolidating forces lifting the closure to module scope AND adding a new import edge from the hub plugin into a CLI-layer/shared module -- a cross-package dependency decision a human should make, hence autofix=false._

Proposed home: propose new shared module packages/kollabor-agent/src/kollabor_agent/process_utils.py::pid_alive (kollabor_agent.runtime already does os.kill(pid,0) liveness and is depended on by both the CLI and the hub plugin); both _pid_alive (cli.py:1288) and _agent_pid_alive (hub/plugin.py:7624) repoint there

**#13 — `_get_loop`** (12 copies)
  - `kollabor/attach_client.py:42` `_get_loop()`
  - `packages/kollabor-agent/src/kollabor_agent/process_manager.py:13` `_get_loop()`
  - `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py:42` `_get_loop()`
  - `packages/kollabor-tui/src/kollabor_tui/render_loop.py:30` `_get_loop()`
  - `packages/kollabor-tui/src/kollabor_tui/fullscreen/session.py:6` `_get_loop()`
  - `packages/kollabor-tui/src/kollabor_tui/fullscreen/plugin.py:6` `_get_loop()`
  - `packages/kollabor-tui/src/kollabor_tui/fullscreen/manager.py:6` `_get_loop()`
  - `plugins/altview/matrix_altview.py:14` `_get_loop()`
  - `plugins/fullscreen/matrix_plugin.py:6` `_get_loop()`
  - `plugins/fullscreen/example_plugin.py:21` `_get_loop()`
  - `plugins/fullscreen/space_shooter_plugin.py:10` `_get_loop()`
  - `plugins/hub/plugin.py:89` `_get_loop()`

_All 12 bodies are byte-identical and semantically identical: a pure asyncio loop getter (try get_running_loop / except RuntimeError -> new_event_loop) with zero UI side effects, the textbook safe_util shape. It is genuine copy-paste, so real_dupe. But the 12 copies span four separately-distributable units (kollabor core app, kollabor_agent, kollabor_tui, root plugins/) and there is NO module they all already import in common (per pyproject: kollabor-agent depends on events/config/ai, kollabor-tui depends only on events), so consolidating creates a NEW cross-package import edge -> cross_package_contract, human call. Two copies also sit in report-only zones: render_loop.py is on the explicit render hot-path list (10 call sites via _get_loop().time()) and plugins/hub/plugin.py:3550 uses it inside hub message-flow (call_soon). Both factors force safe_to_autofix=false._

Proposed home: new shared module, suggest packages/kollabor-events/src/kollabor_events/async_utils.py::get_loop (kollabor_events is the only package every other package + the plugins already transitively depend on); requires human sign-off because it adds a cross-package import contract. Alternatively keep the render_loop.py and plugins/hub/plugin.py copies in place (report-only zones) and only consolidate the other 10.

**#30 — `_has_method, has_method`** (2 copies)
  - `packages/kollabor-config/src/kollabor_config/plugin_config_manager.py:14` `_has_method()`
  - `packages/kollabor-plugins/src/kollabor_plugins/plugin_utils.py:10` `has_method()`

_Both bodies are byte-identical and semantically identical: `return hasattr(obj, method_name) and callable(getattr(obj, method_name, None))` -- "does this object have this callable method." Pure introspection util, no UI side effects, so normally safe_util. But it is NOT mechanically autofixable: kollabor-config declares only `kollabor-events` as a dependency in pyproject.toml, and its sole reference to kollabor_plugins (`from kollabor_plugins.discovery import PluginDiscovery`) is deliberately TYPE_CHECKING-guarded to avoid a runtime coupling. Repointing config's private `_has_method` onto the public `kollabor_plugins.plugin_utils.has_method` would create a new real cross-package runtime dependency. That's a human architecture call, not a mechanical merge -> cross_package_contract, safe_to_autofix=false._

Proposed home: packages/kollabor-plugins/src/kollabor_plugins/plugin_utils.py:10

### Real dupe, non-trivial consolidation

**#14 — `llm_service`** (2 copies)
  - `kollabor/commands/system_commands/handlers/model.py:50` `llm_service()`
  - `kollabor/commands/system_commands/handlers/login.py:43` `llm_service()`

_Byte-identical override-or-registry `llm_service` property copy-pasted across sibling subclasses of BaseCommandHandler (model.py + login.py here; also profile/skills/agent in a defensive variant; system.py registry-only). Not coincidental — base.py:45-48 explicitly comments that subclasses define this accessor, and base.py:38-43 already implements the identical pattern for config_manager. So it's a genuine dupe that belongs pulled up. NOT safe_util: this is an instance property coupled to self._llm_service_override and self.event_bus, not a stateless helper, and consolidation is non-trivial (must add `self._llm_service_override = None` default to base __init__ or SystemCommandHandler — which never sets that attr — would AttributeError; plus the bare model/login form vs the hasattr-guarded profile/skills/agent form must be reconciled). Multi-file, behavioral-init gotcha -> risky_other, autofix false._

Proposed home: /Users/malmazan/dev/kollab/kollabor/commands/system_commands/base.py (add `llm_service` property to BaseCommandHandler next to existing config_manager property at lines 38-43; set self._llm_service_override = None in base __init__ so SystemCommandHandler's registry-only path keeps working)

**#22 — `_fg, fg`** (3 copies)
  - `packages/kollabor-tui/src/kollabor_tui/design_system/inline_widgets.py:29` `_fg()`
  - `packages/kollabor-tui/src/kollabor_tui/status/utils.py:10` `fg()`
  - `plugins/terminal_plugin.py:554` `_fg()`

_All three are byte-identical pure ANSI foreground helpers (f"\033[38;2;{r};{g};{b}m{text}\033[39m") with no UI side effects — genuine copy-paste, not coincidence. Not safe to autofix: layering check shows status/ imports FROM design_system but design_system never imports status, so the AST-flagged "shared" copy (status/utils.py:fg, used by 5 status siblings) is the WRONG canonical — pointing inline_widgets.py (in design_system) at it would invert the dependency arrow. The correct home is design_system (next to its existing solid_fg export), making this a contract-sensitive refactor, not a mechanical repoint. One copy is also a nested closure inside terminal_plugin._render_tmux_widget, requiring hoisting rather than re-pointing. None of the copies sit in the render hot path or hub flow._

Proposed home: packages/kollabor-tui/src/kollabor_tui/design_system/components.py (add public fg() beside solid_fg/C; have status/utils.py:fg re-export from there so the 5 status callers stay unchanged, and switch inline_widgets._fg + terminal_plugin._fg to import it). Do NOT consolidate onto status/utils.py:10 — that inverts the design_system<-status layering.

**#23 — `_extract_thinking`** (2 copies)
  - `packages/kollabor-ai/src/kollabor_ai/response_parser.py:892` `_extract_thinking()`
  - `packages/kollabor-ai/src/kollabor_ai/response_processor.py:93` `_extract_thinking()`

_Identical 4-line body (self.thinking_pattern.findall(content) + strip-filter list comp), identical regex (<think>(.*?)</think>), same _extract_thinking name across two response-handling classes in the same package -- genuine copy-paste, not a coincidental getter. Real dupe but NOT mechanical: response_processor.py / ResponseProcessor is fully dead (never imported, exported, or instantiated anywhere -- grep for import and __init__ exports both empty), so the correct consolidation is deleting the legacy file, not extracting a shared helper. The live copy in response_parser.py feeds the tool-execution pipeline (queue_processor/tool_executor) and reads a per-instance self.thinking_pattern, so any shared helper would need the pattern passed in -- non-trivial and unverifiable by an auto-fixer._

Proposed home: packages/kollabor-ai/src/kollabor_ai/response_parser.py:892 (ResponseParser._extract_thinking is the live source of truth; response_processor.py is unused legacy and should be deleted rather than refactored into a shared util)

### Utility dupe blocked from auto-fix (import-cycle direction or ambiguous home)

**#9 — `llm_service`** (3 copies)
  - `kollabor/commands/system_commands/handlers/skills.py:60` `llm_service()`
  - `kollabor/commands/system_commands/handlers/profile.py:64` `llm_service()`
  - `kollabor/commands/system_commands/handlers/agent.py:63` `llm_service()`

_Genuine copy-paste, not coincidence: agent.py:63, profile.py:64, skills.py:60 (plus login.py:43 and model.py:50 not in the cluster) are all subclasses of the SAME BaseCommandHandler and define a byte-identical override-aware llm_service DI accessor backed by the same self._llm_service_override instance attr. Pure service-registry getter, no UI side effects, not render/hub path -> safe_util family. system.py:37 is a guardless variant of the same intent. Autofix=false because the correct fix edits the shared base __init__ (init _llm_service_override) and removes copies across 5 files, but the cluster only names 3 -- a partial autofix would leave inconsistent state, and touching a constructor shared by 8 subclasses is beyond a mechanical single-symbol repoint._

Proposed home: kollabor/commands/system_commands/base.py:38 (add the override-aware llm_service property to BaseCommandHandler next to the existing config_manager property; init self._llm_service_override=None in base __init__; delete the 5 subclass copies). The hasattr(event_bus,"get_service") guard used in agent/login/profile/skills is the safe superset and subsumes model.py/system.py guardless variants.

**#36 — `_strip_ansi`** (2 copies)
  - `packages/kollabor-tui/src/kollabor_tui/status/widget_picker.py:19` `_strip_ansi()`
  - `plugins/altview/widget_picker_altview.py:19` `_strip_ansi()`

_Both _strip_ansi copies are byte-identical pure ANSI-strip utilities (re.sub(r"\033\[[^m]*m","",text)) with zero UI side effects -- CLAUDE.md explicitly lists "ansi strip" as a safe_util. Genuine copy-paste. But not auto-fixable: the two copies straddle a package boundary (kollabor-tui package vs plugins/altview), and a canonical strip_ansi already exists in status/utils.py:24 with a DIFFERENT regex (r"\033\[[0-9;]*m"), so any repoint is a subtle behavioral substitution + a new plugin->status.utils coupling. When in doubt -> false; a human should pick the canonical regex and approve the plugin dependency._

Proposed home: packages/kollabor-tui/src/kollabor_tui/status/utils.py:24 (existing strip_ansi; layout_renderer.py already imports it via `from .utils import strip_ansi as _strip_ansi`). Both cluster copies should be dropped in favor of this, after reconciling the regex (utils uses [0-9;]*, the copies use [^m]*).

**#38 — `read_key`** (2 copies)
  - `packages/kollabor-tui/src/kollabor_tui/status/modal_presenter.py:179` `read_key()`
  - `packages/kollabor-tui/src/kollabor_tui/status/modal_presenter.py:527` `read_key()`

_Both are byte-identical nested read_key closures decoding terminal escape sequences (os.read + select, returning "ESC[..." strings), one in _show_widget_picker_modal_simple (179) and one in _show_widget_picker_modal_with_row (527). Pure input-decode utility with no render side effects, so safe_util. Not safe_to_autofix: these closures capture fd from enclosing modal input loops, sit in interactive tmux-dependent code that needs a live test the autofixer can't run, and a correct consolidation should also fold the near-identical 3rd copy at line 283 (which returns KeyPress objects, not strings) — a judgment call, not a mechanical 2-way merge._

Proposed home: new module-level helper packages/kollabor-tui/src/kollabor_tui/status/modal_presenter.py:_read_escape_key(fd) — fold lines 179 and 527 into it

---

## Dismissed (not real duplicates)

### Intentional (framework overrides, same-file alias pairs, abstract hooks)

- **#12 `get_headers`** — base.py:209 is a non-abstract DEFAULT impl on the ABC BaseAPIAdapter; openai_adapter.py:317 is a subclass override in OpenAIAdapter(BaseAPIAdapter). They are byte-identical only because OpenAI uses standard Bearer auth, which equals the base default. The sibling anthropic_adapter.py:425 overrides the same method with genuinely different headers (x-api-key + anthropic-version), proving this is a polymorphic base-default + per-subclass override pattern, not copy-paste. Deleting the OpenAI override would silently couple OpenAI auth to any future change in the base default -- a contract change, not a mechanical merge.
- **#21 `handle_input`** — handle_input(key_press)->bool is an abstract/overridable framework hook on FullScreenPlugin (base stub at packages/kollabor-tui/.../fullscreen/plugin.py:116 just `pass`es). ~25 plugins/altviews override it; matrix and space_shooter both implement the trivial "exit on q/ESC, else continue" body, so their 5-line bodies coincidentally collide. Required override, not copy-paste — merging would break the override contract and add a pointless cross-file dep between two unrelated demo plugins.
- **#28 `on_stop`** — All three are identical framework-override lifecycle hooks (async def on_stop(self): await super().on_stop()) in three different FullScreenPlugin subclasses. This is the exact "async super() override" pattern the schema flags as intentional, not copy-paste logic. The canonical on_stop already lives in the base class (kollabor_tui/fullscreen/plugin.py:141); these overrides are per-plugin extension points (matrix and example even have commented-out customization slots). Merging them would mean deleting valid overrides, not consolidating duplicated behavior.
- **#40 `_sync_command_mode, command_mode`** — Same-file pair in input_handler.py: line 234 is the @command_mode.setter (descriptor protocol, fires on `ih.command_mode = X`), line 241 is `_sync_command_mode`, a plain method registered at line 177 as a callback handed to ModalController to push command_mode back into the handler. Identical bodies by necessity (both fan the value to _command_mode_handler.command_mode and _command_mode_local) but they are distinct collaboration points — a property setter cannot be passed as a callable, and the property cannot be deleted without breaking `ih.command_mode = ...` assignments. This is exactly the setter + _sync_ variant intentional pattern; not mergeable.
- **#41 `safe_ver`** — Both safe_ver are nested closures, each scoped inside its own FastAPI route handler in the same file: version() at server.py:121 and status() at server.py:142. Same path, two line numbers, two nested closures -- the exact same-file "nested closures" pattern the task flags as intentional. Each is a 4-line local wrapper around importlib.metadata.version with an "unknown" fallback; they are not module-level functions and have zero external call sites to repoint. Consolidating would require hoisting to module scope and deleting redundant inner imports (a restructure, not a mechanical repoint), so even if read as real_dupe it is not auto-fixable.
- **#42 `get_all, load`** — Same-file, same-class alias pair on CrystalStore: load() (line 207) and get_all() (line 350), both bodies `self._ensure_loaded(); return list(self._entries)`. Per rubric this is the publish/heartbeat-style alias archetype and same-file pairs lean intentional. Both names have live call sites with distinct usage idioms (load() reads as "construct+load from disk", get_all() as accessor on a live store), the body is 3 trivial lines with near-zero drift risk, and there's no benefit to forcing a rename. Leave alone.
- **#43 `heartbeat, publish`** — Same-file sibling alias pair on PresenceManager (plugins/hub/presence.py): publish() = initial write of the presence file, heartbeat() = periodic refresh. Bodies are identical (set last_heartbeat, _atomic_write) but the two names document distinct caller intent and are exactly the publish/heartbeat alias pattern flagged as intentional. They are part of the hub presence/discovery flow, so even if collapsed they would be hub_flow (report only). Leave alone.
- **#44 `_extract_global_vault_write, _extract_vault_write`** — Two sibling nested closures defined back-to-back in the same hub plugin registration method. Bodies match because both parse the identical keywords+content attribute shape, but they are bound to distinct tags (vault_write -> project crystal store; global_vault_write -> cross-project global store) via separate register_plugin_tag calls. Same-file closure pair wired to different semantics = intentional, and it lives in hub plugin tag/routing flow which CLAUDE.md marks REPORT ONLY.
- **#45 `can_cycle_next, can_cycle_prev`** — Same-file next/prev predicate pair on the ToggleHandler class (can_cycle_next / can_cycle_prev). Bodies coincide (both `return len(self.states) > 1`) because for a toggle, forward and backward cycling have identical availability, but they are two distinct public API methods that are supposed to exist separately, mirroring the adjacent is_first_state/is_last_state pair. This is the classic intentional next/prev predicate archetype, not a copy-paste dupe to merge.

### Coincidental (identical body, different semantic contract)

- **#15 `_get_context_service`** — Both are byte-identical 4-line service-accessor idioms (null-check self.event_bus, return self.event_bus.get_service("context_service")), but each is bound to a different class's own event_bus in a different package: FileOperationsExecutor in kollabor-agent vs HubPlugin in plugins/hub. This is a coincidentally-identical per-class getter, not copy-pasted shared logic with a natural home. A third sibling (kollabor/commands/system_commands/handlers/context.py:53) already diverges with extra mock-filtering, confirming it's a recurring idiom rather than one canonical function. Merging would force a cross-package dependency for a trivial null-check, and one copy lives in HubPlugin (hub flow = report-only per CLAUDE.md). Leave alone.
- **#18 `is_alive`** — Different module contract: TerminalSession.is_alive (terminal_plugin.py:75) is a regular METHOD (callers invoke with parens, e.g. session.is_alive()), while AgentSession.is_alive (agent_orchestrator/models.py:44) is a @property (callers read it without parens, e.g. agent.is_alive). You cannot collapse a method and a property into one shared definition without breaking one caller set, so this is not a consolidatable dupe. Secondary support: the body is the generic Popen.poll() liveness idiom that recurs independently across unrelated dataclasses (also hub/presence.py and process_manager.py on different classes), not a single source of truth that drifted.
- **#26 `_request_render, request_render`** — Both bodies are the same trivial guarded-delegation idiom (if self._render_loop and hasattr(..., "request_render"): self._render_loop.request_render()), but they belong to unrelated classes with different contracts: LayoutManager._request_render is a PRIVATE internal helper (paired with set_render_loop()), while AltView.request_render is a PUBLIC method on the AltView(ABC) framework base class (paired with _set_render_loop()) that subclasses call to redraw. Different hierarchy, different visibility, different injection setter -- coincidental idiom match, not copy-paste. Additionally render_layout.py is an explicit CLAUDE.md render hot-path file (REPORT ONLY), so consolidation is forbidden regardless.
- **#33 `set_render_loop`** — Two trivial dependency-injection setters (self._render_loop = render_loop) on two unrelated classes: LayoutManager in render_layout.py and DisplayController in display_controller.py. Each owns its own _render_loop field and its own consumer (LayoutManager._request_render at line 367; DisplayController request_render at line 162-163), and each is wired independently in application.py. The byte-identical body is the standard DI-setter convention, not copy-pasted logic — five such set_render_loop methods exist across the package (also message_coordinator.py:921, script_refresh_scheduler.py:139, altview base _set_render_loop:107). render_layout.py is an explicit render hot path file per CLAUDE.md, so even if one wanted to merge, it would be report-only.
- **#34 `set_event_bus`** — Both bodies are the trivial DI setter `self._event_bus = event_bus`, but they live on two unrelated classes in different packages: DisplayController (kollabor-tui input subsystem) and HubConsoleAltView (plugins/altview). This is a ubiquitous dependency-injection convention (set_event_bus also exists in kollabor-ai context_service/service.py, and inline `self._event_bus =` assignments are everywhere), not copy-paste. There is no shared logic to extract; consolidating would force an artificial base class/mixin spanning kollabor-tui and plugins/altview to dedupe a single line, creating a new cross-package dependency for zero benefit.
- **#35 `is_active`** — Both are trivial `return self.active` getters but on unrelated classes with different semantics: navigation_state.py:160 is on the StatusNavigationState dataclass where `active` means navigation focus (STATUS_FOCUS vs INPUT mode); renderer.py:329 is on FullScreenRenderer where `active` means the renderer is currently drawing. A 3-line single-attribute getter matching across two unrelated classes is a coincidence, not copy-paste. Consolidating would require forcing an artificial shared base/mixin onto semantically distinct objects — net negative. Leave alone.
- **#37 `get_selected_widget, selected_widget_id`** — Both bodies are `return self._result`, but they are different members of unrelated classes with different contracts: WidgetPickerModal.get_selected_widget() is a regular method (legacy LiveModal picker in kollabor-tui), while WidgetPickerAltView.selected_widget_id is a @property (new AltView-stack picker in plugins/altview). Different names, different invocation style (method call vs attribute access), different base classes, no shared callers. The match is coincidental trivial-getter shape, not copy-pasted logic worth merging; consolidating would mean unifying two whole picker classes across a package/plugin boundary.
- **#39 `get_key`** — The two get_key methods belong to DIFFERENT classes in the same file: EncryptedFileKeyStorage.get_key (line 306) and PlaintextKeyStorage.get_key (line 514). They are sibling implementations of a common keystore-backend interface (there are 4 such get_key methods total: APIKeyManager:117, EncryptedFileKeyStorage:306, EnvironmentKeyStorage:381, PlaintextKeyStorage:514). The bodies are byte-identical only because the encrypt-vs-plaintext asymmetry lives in each class's own _load_keystore/_save_keystore, not in get_key. Their semantic contracts differ (returns decrypted key vs returns insecure plaintext key) and APIKeyLoader deliberately fans out to all backends in priority order. Coincidental match, not copy-paste; merging would collapse the polymorphic Strategy interface.