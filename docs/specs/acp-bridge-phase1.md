# ACP bridge phase 1

Phase 1 is an outbound, local stdio bridge. It does not implement inbound ACP,
network routing, hub message/ask, or Web UI integration.

## frozen contracts

- `ACPProfile` stores a portable adapter command, optional cwd/workspace, explicit
  environment, and a `pass_env` allowlist. Device-local command resolution occurs
  immediately before spawn. API keys are not inherited unless explicitly allowed.
- One `ACPRunner` owns one child process and one ACP session. It performs
  `initialize`, `session/new`, and `session/prompt` over newline-delimited JSON.
- Prompts are FIFO with a maximum queue depth of 10. Generation IDs are monotonic;
  each result and hidden `turn_end` frame carries its generation and session ID.
  There are no silent retries.
- Stream updates are display/history data only. They are never parsed as Kollab
  tags, prompts, or tool instructions. Only text message chunks contribute to the
  assembled answer; thought, plan, and tool updates remain typed metadata.
- Permission responses are first-answer-wins and accept only offered opaque
  `optionId` values. Phase 1 supports `allow_once` and `reject_once`; no reusable
  grants are persisted.
- `kollab.mcp_hub` is a minimal authenticated stdio server exposing only
  `hub_status`; stdout is protocol-only and diagnostics go to stderr.

The CLI exposes `--acp-command` plus `--acp-prompt`; `--acp-profile PATH`
loads the same portable JSON profile shape and still resolves its executable
locally. A profile command is required; no provider credentials are inferred.

The bridge uses a narrow tested JSONL boundary because the pinned
`agent-client-protocol==0.12.1` package does not expose the current notice schema
through its typed Python API. Notice capability advertisement is therefore not
claimed by this phase.
