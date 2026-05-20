# Runtime Smoke PRD

## Objective

Create a reusable full-runtime smoke that proves a fresh Kollab install path can:

1. start from an isolated empty home/config
2. launch with a mock MCP server
3. execute one XML tool, one native tool, and one MCP tool
4. launch daemon/attach
5. switch profile, agent, and permission mode through attach where supported
6. survive daemon restart/reconnect without losing visible state
7. confirm status, hub roster, delivery trace, and render output stay coherent

## User Impact

This becomes the high-confidence "new user path still works" proof before
future hub/tool/state changes merge.

## Scope

- Prefer extending `tests/tmux/` and `scripts/stabilization-gate.sh`.
- Use existing mock MCP helpers where possible.
- Keep the smoke deterministic and self-cleaning.
- Avoid real network calls and provider requests.
- Do not hide hub join visibility; agents should be allowed to announce.

## Acceptance Criteria

- A single command runs the smoke from the repo root.
- It uses an isolated temp `HOME`/Kollab home.
- It starts or configures a mock MCP server.
- It proves XML, native, and MCP tool execution through real runtime paths.
- It proves daemon attach can render `/status` and hub/status state.
- It is added to the focused stabilization gate only if runtime is stable enough.
- The PR includes evidence from local execution.

## Validation

- Run the new smoke directly.
- Run the focused stabilization gate.
- Run py_compile or shellcheck-style validation for touched scripts where possible.

## Deliverable

Open a PR from `codex/runtime-smoke` to `main` with a concise summary,
validation evidence, and any remaining limitations.
