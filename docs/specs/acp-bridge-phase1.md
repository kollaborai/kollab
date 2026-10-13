# ACP bridge phase 1 checkpoint

This document records the current implementation checkpoint; the canonical
phase contract remains in `docs/specs/acp-bridge.md`.

implemented in commits 22da746d and 854d3dbb:

- portable `ACPProfile` command/cwd/workspace/env/pass_env resolution with
  provider-key stripping and local executable lookup
- pinned `agent-client-protocol==0.12.1` dependency and typed SDK
  `spawn_agent_process`/`Client` integration for initialize, session/new,
  prompt, session updates, permission callback, cancel, and process cleanup
- FIFO prompt queue bounded at ten entries, generation/session identifiers,
  typed final answer and correlated hidden turn-end frame
- fail-closed permission bridge using nested `AllowedOutcome`/`DeniedOutcome`
  and offered opaque `optionId` validation for `allow_once`/`reject_once`
- standard MCP initialize/initialized/tools/list/tools/call server exposing
  `hub_status`, with environment-bound token/generation/socket checks
- CLI flags `--acp-command`, `--acp-profile`, `--acp-prompt`, and hidden
  `--mcp-hub`; focused SDK fake and MCP protocol tests

validation checkpoint:

- 83 focused tests passed, including ACP/MCP, CLI, and input-pipeline suites
- `ruff`, `py_compile`, and `git diff --check` passed on changed files
- `kollab --version`, `kollab --hub status`, and a live MCP stdio
  initialize/list/call smoke test passed

unresolved gates:

- the installed `claude` and `codex` binaries report versions but have not been
  qualified as ACP adapters; a real adapter/login run is still required
- canonical `--as`/`--llm` daemon-path integration is not yet complete
- reusable permission grants, steering, inbound ACP, hub message/ask, network
  routing, Web UI integration, and broad profile support remain later phases
- process-group/stderr and SDK behavior need adversarial review against the
  current host adapter before claiming production readiness
