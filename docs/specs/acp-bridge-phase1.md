# ACP bridge phase 1 checkpoint

This document records the current implementation checkpoint; the canonical
phase contract remains in `docs/specs/acp-bridge.md`.

implemented in commits 22da746d, 854d3dbb, 557fdebe, fca1178f,
30ddd1b0, 02a7b848, 0f112962, a51d658d, and ebbf1e2f:

- portable `ACPProfile` command/cwd/workspace/env/pass_env resolution with
  provider-key stripping and device-local executable lookup
- built-in portable `claude-code`, `codex-cli`, and `gemini-cli` profiles with
  pinned install guidance for missing adapters
- device-local `~/.kollab/acp/resolved.json` cache recording command identity,
  symlink-preserving executable directory, and optional Node resolution;
  vanished or changed command paths are re-resolved, and adapter/Node
  directories prefix the child PATH
- pinned `agent-client-protocol==0.12.1` dependency and typed SDK connection
  for initialize, session/new, prompt, session updates, permission callback,
  cancel, process-group cleanup, and stderr draining
- FIFO prompt queue bounded at ten entries, process/session generation fencing,
  per-turn IDs, cancel rebasing for queued turns, typed final answer, and a
  correlated hidden turn-end frame
- fail-closed permission bridge using nested `AllowedOutcome`/`DeniedOutcome`,
  offered opaque `optionId` validation, timeout/cancel settlement, and
  allow_once/reject_once handling
- standard MCP SDK initialize/initialized/tools/list/tools/call server exposing
  `hub_status`; every call rechecks a parent AgentSocketServer binding over a
  temporary Unix socket and receives the current roster payload
- CLI flags `--acp-command`, `--acp-profile`, `--acp-prompt`, and hidden
  `--mcp-hub`; SDK fake, queue/cancel, permission, parent binding, revocation,
  and MCP ClientSession tests

validation checkpoint:

- 24 ACP bridge/profile tests pass, with focused ACP/MCP/CLI/input suites green
- `ruff`, `py_compile`, and `git diff --check` pass on changed files
- `kollab --version`, `kollab --hub status`, and live MCP ClientSession
  initialize/list/call plus parent roster-revocation smoke pass

remaining phase-1 gates (not complete):

- canonical `--as`/`--llm` daemon/profile integration is not wired; current CLI
  flags are a one-shot harness path only
- real streaming/history/tool-card mapping, immutable origin/Hub final/end
  frame, lifecycle/steering/resume behavior, permission windows/grants/waiting
  line, and a real adapter run still need implementation/qualification
- installed `claude` and `codex` binaries report versions but have not been
  qualified as ACP adapters; no login/billing or fake proof substitutes this
- inbound ACP, hub message/ask, network routing, Web UI integration, reusable
  grants beyond the phase-1 option bridge, and broad profile support remain
  later phases in the canonical plan
