# Web UI agent capture normalization

Status: proposed
Owner: Kollab web UI + hub maintainers

## Problem

`hub_capture` is optimized for terminal inspection, not browser rendering. The
orchestrator captures terminal-oriented output and returns a string preview:
`_capture_output()` keeps only the last ten lines, while the hub plugin adds a
wrapper and truncates the result at 10,000 characters. Captured lines may contain
ANSI color/control sequences and tool-renderer fragments such as `Tool result:`
headers. A Web UI that displays this value as plain text exposes escape codes and
implementation details instead of a readable session timeline.

The socket-level terminal API remains unchanged. This spec defines normalized
browser data and a shared transcript view rather than reusing terminal capture
text. The CLI and plugin transcript defaults are specified below.

## Goals

- Render captured agent activity as readable text in the Web UI.
- Preserve event ordering, timestamps/duration when available, tool names, and
  success/error state.
- Keep ANSI/control sequences out of browser payloads.
- Avoid truncating a useful structured response merely because terminal preview
  formatting was applied first.
- Share bounded transcript rendering across `kollab --hub capture`,
  `/hub capture`, and the `hub_capture` tool while preserving `--raw` terminal
  output.

## Non-goals

- Replacing the existing terminal/tmux capture implementation.
- Reconstructing persisted conversation history, system prompts, or credentials.
- Adding a second agent execution or polling protocol.
- Reproducing terminal colors in the browser.

## Proposed contract

Add opt-in `plain` and `json` formats to the existing hub capture/console
consumer path. The socket-level default terminal format remains unchanged.
Browser/API clients select `format=plain` or `format=json` (the `/hub console`
surface uses the same opt-in formats). The socket JSON format returns normalized
event records as shown below. The CLI, `/hub capture`, and `hub_capture` tool use
the same bounded transcript renderer by default; `kollab --hub capture ...
--raw` explicitly selects the existing terminal path.

```json
{
  "agent": {"identity": "lapis", "status": "working", "duration": "2m 14s"},
  "lines": [
    {
      "seq": 1,
      "timestamp": "2026-10-12T22:30:01Z",
      "kind": "tool-call",
      "tool": "file_read",
      "text": "Read docs/specs/assistant-ui-migration.md",
      "status": "ok"
    },
    {
      "seq": 2,
      "timestamp": "2026-10-12T22:30:02Z",
      "kind": "tool-result",
      "tool": "file_read",
      "text": "Read 220 lines",
      "status": "ok"
    }
  ],
  "truncated": false,
  "next_cursor": null
}
```

`kind` is one of `text`, `tool-call`, `tool-result`, `status`, `error`, or
`message`. User and assistant message records retain their `role` when available.
Unknown source records must degrade to `text` without failing the request. `text`
is already normalized to UTF-8, stripped of ANSI CSI/OSC/control sequences, and
has terminal carriage-return redraws resolved. The transcript renderer labels
user/assistant messages, tool calls/results with status, and status/error events;
it omits tool arguments and reasoning events.

The `plain` mode returns the same normalized content as newline-delimited UTF-8
text, without JSON metadata or terminal color/control sequences. It is intended
for `/hub console`, browser text panels, shell pipelines, and simple API clients.

The endpoint may initially derive records from the existing captured stream, but
its response shape must be stable enough for a later native event source.

## Normalization rules

1. Parse structured JSON/event records when present; do not stringify the whole
   object into one browser line.
2. Strip ANSI CSI, OSC, SGR, cursor movement, and non-printing control bytes.
3. Convert CR-based progress redraws to the final visible line.
4. Remove transport wrappers (`[hub_capture]`, `[capture: ...]`) from content and
   expose metadata in `agent` instead.
5. Remove duplicate `Tool result:` prefixes only when they are transport labels;
   preserve user/model text that literally begins with that phrase.
6. Preserve newest-first/oldest-first semantics explicitly via `order`; Web UI
   must never infer order from an incidental array reversal.
7. Apply pagination before serialization limits. Return `truncated` and
   `next_cursor` rather than silently clipping a record.
8. The common transcript renderer respects line and character budgets and keeps
   the pagination marker when a record or page is truncated.
9. The Hub display tap is a bounded 200-event in-memory ring. Mark capture as
   truncated when older events have been evicted. `next_cursor` is process-local
   and must not be persisted or reused after an engine restart.

## Compatibility and security

- CLI `--hub capture`, `/hub capture`, and `<hub_capture>` use the bounded
  transcript renderer by default. `kollab --hub capture ... --raw` retains the
  prior terminal output path.
- Socket `format=plain`, `format=json`, and terminal requests retain their
  existing API semantics; the `/hub console` and browser/API opt-in formats do
  not change.
- The Web UI endpoint is scoped to identities visible to the requesting session
  and must reject unknown/offline targets with a typed 404 response.

- The shared transcript includes user-authored text from bounded in-memory
  capture events and is sensitive; it omits tool arguments and reasoning.
- Do not include environment variables, access tokens, prompt secrets, or raw
  relay payloads in normalized records.
- Escape text at the React rendering boundary; never inject captured text as HTML.

## Frontend behavior

- Display one row/card per normalized record with a monospace text region.
- Render tool calls and results with existing tool-summary affordances.
- Show agent status/duration in a header, not as captured text.
- Show an explicit `capture truncated` indicator with a refresh/load-more action.
- Preserve raw text in a disclosure for debugging, but only after normalization;
  raw ANSI bytes are not rendered by default.
- On malformed records, show the line as `text` and continue rendering others.

## Tests

Backend:

- ANSI CSI/OSC/control-sequence stripping (including split sequences).
- CR redraw handling and empty-line preservation.
- Wrapper removal and metadata extraction.
- Structured tool-call/tool-result mapping and unknown-record fallback.
- Pagination/cursor and typed unknown/offline target errors.
- CLI default and raw dispatch, transcript labels, tool errors, truncation,
  ANSI/OSC/control cleanup, and user-message content.
- Explicit plain and terminal transport snapshots remain unchanged.

Frontend:

- Fixture with the captured output shown in the bug report renders no `\\x1b` or
  terminal glyphs.
- Tool-call/result rows are readable and ordered.
- Truncation indicator and load-more behavior.
- Malformed/unknown records do not blank the whole capture panel.

## Rollout

1. Add normalization helpers and backend response tests.
2. Add a Web UI endpoint or JSON mode behind the existing session API.
3. Switch the Web UI capture panel to normalized records and add fixtures.
4. Compare CLI default and `--raw` snapshots; only the default changes to the
   shared transcript, and the raw terminal path remains unchanged.
5. Remove any temporary raw-stream fallback after one release cycle.
