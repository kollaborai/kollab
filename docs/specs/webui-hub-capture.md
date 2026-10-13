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

The terminal/CLI contract must remain unchanged. This spec defines a browser
boundary that returns normalized data rather than reusing terminal capture text.

## Goals

- Render captured agent activity as readable text in the Web UI.
- Preserve event ordering, timestamps/duration when available, tool names, and
  success/error state.
- Keep ANSI/control sequences out of browser payloads.
- Avoid truncating a useful structured response merely because terminal preview
  formatting was applied first.
- Keep `hub_capture` CLI/XML output backwards compatible.

## Non-goals

- Replacing the existing terminal/tmux capture implementation.
- Exposing private conversation history or credentials.
- Adding a second agent execution or polling protocol.
- Reproducing terminal colors in the browser.

## Proposed contract

Add opt-in `plain` and `json` formats to the existing hub capture/console
consumer path. The default terminal format remains unchanged. Browser/API
clients select `format=plain` or `format=json` (the `/hub console` surface uses
the same opt-in formats). The JSON mode returns:

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
`message`. Unknown source records must degrade to `text` without failing the
request. `text` is already normalized to UTF-8, stripped of ANSI CSI/OSC/control
sequences, and has terminal carriage-return redraws resolved.

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

## Compatibility and security

- Existing `/hub capture`, `<hub_capture>`, and CLI `--hub capture` continue to
  return the current terminal preview format when no format is supplied.
- `plain` and `json` are explicitly opt-in and may be selected by `/hub console`
  and browser/API consumers without changing the default terminal contract.
- The Web UI endpoint is scoped to identities visible to the requesting session
  and must reject unknown/offline targets with a typed 404 response.
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
- Existing CLI/XML hub_capture snapshots remain unchanged.

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
4. Compare terminal `hub_capture` snapshots before/after; no CLI output changes.
5. Remove any temporary raw-stream fallback after one release cycle.
