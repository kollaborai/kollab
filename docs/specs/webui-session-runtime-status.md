# Web UI session runtime/status sidebar

Issue: #473
Status: R2 follow-up revision for two remaining findings; fresh verdict requested; not passed
Last updated: 2026-10-31

## Goal

Under the existing `Model: <effective model>` row in the selected session sidebar,
show a compact, read-only runtime summary. Expand it to show the supported status
values represented by the default TUI layout, including context policy, token
usage, cache metrics, runtime, agent, MCP, workspace, and hub status.

The sidebar integrates and persists the numerical context/usage view alongside
the existing transient context-compaction banner. It must not imply that reaching
a token threshold means compaction is currently running.

Model and Effort controls are explicitly out of scope for #473. Do not add,
change, or reset provider/profile/session selectors here. Those questions belong
to mockup-first issue #475.

## Widget scope and source limits

The default TUI layout has 15 unique widget ids; MCP appears in two rows and is
shown once in this sidebar. The in-scope widgets are cwd, git branch, profile,
effective model, processing status, daemon microphone state, active agent,
active skills, session display name, MCP, usage stats, context-compaction, and
hub identity/peer count. Context-compaction is plugin-provided and is in scope
when its status service is available.

This is the default layout's supported status data, not a built-in-only list.
It does not add arbitrary/custom plugin widgets. A disabled or unavailable
in-scope source is shown as visibly unavailable; it is never replaced with a
plausible-looking zero or a fabricated status.

Two default-layout displays are excluded until they have an authoritative
selected-daemon producer:

- Git working-tree status (clean/modified/added/deleted counts). The TUI gets
  this from its local git-status cache; the current engine state RPC only
  supplies daemon cwd and git branch. Do not run Git on the Web UI engine host
  and present that as the selected daemon's status. A later change may add a
  selected-daemon Git status producer and contract.
- Deep Thought status. Deep Thought registers a TUI widget in
  `plugins/deep_thought/plugin.py`, but the current engine/session state API
  has no selected-daemon status source for it. Do not add decorative or
  client-local Deep Thought values. Revisit only after a daemon state producer
  exists and is reviewed.

## Ownership and data flow

The engine session-status assembler is the sole owner of the Web UI snapshot.
It assembles data for the requested engine session from that session's selected
daemon through its state service. It must not read global/client state or derive
selected-daemon values from the engine host. Each source retains its own
observation time and freshness state; one failed source does not discard the
other successfully read sections.

`GET /sessions/{engine_session_id}/state` returns a versioned
`runtime_status` snapshot. Live `session_runtime_status` SSE updates use the
same snapshot schema and identity fields. Existing endpoint fields may remain
for current consumers, but the new sidebar reads only `runtime_status`.

| Snapshot section | Authoritative source and rules |
| --- | --- |
| Engine session identity | The engine session registry and route path. |
| Conversation identity/display name | The selected daemon's current conversation id. `session_display_name` follows the TUI `render_session` rule: when the id begins with exactly ten digits and a hyphen, remove that timestamp prefix; then remove a final hyphen plus four alphanumeric characters only when the first of those four characters is a digit. Otherwise preserve the id. Responsive truncation is a UI concern, not an API value. |
| Session attachment identity and ordering | The engine session-status assembler owns a monotonically increasing `attachment_epoch` scoped to `engine_session_id`, incremented under one session-state lock whenever the selected daemon generation or conversation changes. `daemon_generation` is a unique opaque id, not a PID. `revision` orders payload changes only within `(engine_session_id, attachment_epoch)` and is not identity. A higher epoch is authoritative only when delivered in a complete snapshot/resync or an explicit identity-transition event. |
| Session access | The server creates a trusted `SessionReadContext` after authenticating the browser, PhoneLink peer, or remote peer; client-supplied principal/access-path fields are never trusted. An engine-owned session ownership/share policy issues an opaque `SessionReadGrant` for an exact engine session and attachment. A requested session ID selects a target but grants no access; authorize GET, SSE subscription, and every event against the context and grant, including revocation. |
| Profile/effective model | Native mode: selected daemon's active profile/state. ACP mode: the model reported by the active ACP harness when present, not the saved profile default. Preserve the source and report unknown if the active model is not reported. |
| Native context/compaction | The selected daemon's `ContextCompactionPlugin` runtime snapshot. The assembler preserves the separate measurements and policy source defined below. If the plugin is disabled or unavailable, expose that explicitly. |
| Native usage | The selected daemon's session statistics, with the measurement scope, provenance, and completeness defined below. The stats producer must preserve missing data rather than coercing it to zero. |
| ACP context/usage | The selected ACP harness's `usage_update` data only. Do not label its `used` value as native prompt tokens, infer native compaction thresholds, or infer a currency. |
| Processing status | The selected daemon's processing-state snapshot, with independent health. `is_processing` maps to Ready/Working only when sampled; on a failed read set source health to error and expose no false/Ready current value (unknown if never sampled, otherwise a stale last sample). It is separate from compaction status. |
| Cwd and git branch | The selected daemon's system-info snapshot. Never substitute engine-host cwd/branch or a global cache. |
| Daemon voice | The selected daemon's voice plugin/service, with independent `source_health.voice`; normalize status only. On a missing producer report unavailable; on status failure report source error and unknown requested/running/stage values (or stale last-good values), never synthetic false/off. Exclude transcript and raw activity payload. |
| Agent and skills | The selected daemon's active-agent snapshot, including active skill names only. |
| MCP | The selected daemon's MCP state, normalized to the enum below. Existing totals alone are insufficient if they cannot distinguish off, error, and enabled-but-zero-connected. |
| Hub | The selected daemon's hub snapshot, projected to identity, coordinator flag, and peer count. Do not expose remote roster details in this sidebar contract. |

## Snapshot contract

The following is the normative logical shape. Names may be mapped to the
project's DTO conventions, but implementations must preserve these distinctions
and meanings. The initial GET and every live update use the same schema.

```ts
interface Metric<T> {
  value: T | null;
  state:
    | "reported"
    | "estimated"
    | "derived"
    | "unknown"
    | "not_applicable";
  completeness: "complete" | "partial" | "unknown" | "not_applicable";
  source:
    | "native_provider"
    | "native_runtime"
    | "acp_harness"
    | "local_estimate"
    | "policy"
    | "unknown"
    | "not_applicable";
  freshness: SourceState;          // value-level freshness, not collection health
  observed_at: string | null;      // actual sample time for this value, ISO-8601 UTC
}

type SourceState = "fresh" | "stale" | "unavailable" | "error" | "not_applicable";

interface SourceHealth {
  state: SourceState;
  observed_at: string | null;       // ISO-8601 UTC
  stale_after_ms: number | null;    // producer-defined; no fabricated freshness
  error_code: string | null;        // allowlisted, safe code; never raw exception text
}


interface CostMetric {
  amount: number | null;
  currency: string | null;
  state: "priced" | "estimated" | "partial" | "unpriced" | "unknown" | "not_applicable";
  completeness: "complete" | "partial" | "unknown" | "not_applicable";
  source: "native_provider" | "native_runtime" | "acp_harness" | "local_estimate" | "unknown" | "not_applicable";
  freshness: SourceState;
  observed_at: string | null;
}


interface SessionRuntimeStatusSnapshot {
  schema_version: 1;
  engine_session_id: string;
  conversation_id: string | null;
  session_display_name: string | null;
  daemon_generation: string;
  attachment_epoch: number;      // monotonic per engine session; changes on daemon/conversation identity change
  revision: number;              // ordering only within one attachment_epoch, not an identity field
  observed_at: string;              // ISO-8601 UTC, snapshot assembly time

  source_health: {
    identity: SourceHealth;
    model: SourceHealth;
    context_compaction: SourceHealth;
    usage: SourceHealth;
    processing: SourceHealth;
    voice: SourceHealth;
    system: SourceHealth;
    agent: SourceHealth;
    mcp: SourceHealth;
    hub: SourceHealth;
  };

  model: {
    profile_name: Metric<string>;
    provider: Metric<string>;
    effective_model: Metric<string>;
    source: "native_profile" | "acp_harness" | "unknown";
  };

  context: {
    mode: "native" | "acp" | "unknown";
    native: {
      model_context_window_tokens: Metric<number>;
      last_request_prompt_tokens: Metric<number>;
      current_history_prompt_estimate_tokens: Metric<number>;
      outgoing_prompt_estimate_tokens: Metric<number>;
      token_gate_prompt_tokens: Metric<number>;
      automatic_compaction_threshold_tokens: Metric<number>;
      automatic_compaction_enabled: Metric<boolean>;
      automatic_compaction_disabled_reason:
        Metric<"configuration" | "session_failures">;
      threshold_source: "automatic" | "manual" | "fallback" | "unknown" | "not_applicable";
      tokens_until_compaction_gate: Metric<number>;
      hard_cap_tokens: Metric<number>;
      status:
        | "idle"
        | "compacting"
        | "ready_to_retry"
        | "applied"
        | "failed"
        | "paused"
        | "warning"
        | "rolled_over"
        | "unknown"
        | "unavailable"
        | "not_applicable";
      message_code: string | null;   // allowlisted UI message key, not plugin prose
      action_code: string | null;    // allowlisted UI action key, not arbitrary text
    };
    acp: {
      context_used: Metric<number>;
      context_limit: Metric<number>;
      cost_amount: Metric<number>;
      cost_currency: string | null;
    };
  };

  usage: {
    message_count: Metric<number>;
    last_request_input_tokens: Metric<number>;
    last_completed_turn_id: Metric<string>;
    last_completed_turn_output_tokens: Metric<number>;
    last_completed_turn_cache_read_tokens: Metric<number>;
    last_completed_turn_cache_creation_tokens: Metric<number>;
    cumulative_input_tokens: Metric<number>;
    cumulative_output_tokens: Metric<number>;
    cumulative_cache_read_tokens: Metric<number>;
    cumulative_cache_creation_tokens: Metric<number>;
    last_completed_turn_cost: CostMetric;
    cumulative_cost: CostMetric;
    last_completed_turn_thinking_duration_seconds: Metric<number>;
  };

  runtime: {
    is_processing: Metric<boolean>;
    cwd: Metric<string>;
    git_branch: Metric<string>;
    daemon_voice: {
      requested: Metric<boolean>;
      running: Metric<boolean>;
      stage: Metric<
        | "off"
        | "starting"
        | "listening"
        | "hearing"
        | "transcribing"
        | "speaking"
        | "enrolling"
        | "voiceprint_saved"
        | "error"
      >;
    };
    active_agent: Metric<string>;
    active_skills: Metric<string[]>;
    mcp: {
      state: "off" | "error" | "zero_connected" | "connected" | "unavailable";
      connected_server_count: Metric<number>;
      tool_count: Metric<number>;
    };
    hub: {
      identity: Metric<string>;
      is_coordinator: Metric<boolean>;
      peer_count: Metric<number>;
    };
  };
}
```

### Measurement semantics

- A zero is a measured zero only when `state` is `reported` or `derived` and
  `completeness` permits it. Missing provider/harness data is `value: null` with
  `state: unknown`; it is never converted to numeric zero. Not-applicable is
  distinct from unknown.

- Metric `state` is provenance, while `freshness` and metric `observed_at` describe
  the age of that value. `SourceHealth` separately describes the latest source
  collection attempt. A retained value after a failed refresh has source health
  `error` and metric freshness `stale`, keeping its original sample timestamp; a
  never-sampled value is null/unknown with freshness `error`. A missing producer
  is unavailable. Stale values may be shown as historical, but must not feed a
  current-token gate, current-turn counter, or other live calculation.

- `last_request_prompt_tokens` is the prompt count for the most recent native
  provider request. It is not a post-compaction count and may become stale after
  compaction or history replacement.
- `current_history_prompt_estimate_tokens` is a fresh local estimate of the
  history currently held by the selected daemon. It is not provider-reported
  usage and must be marked estimated.
- `outgoing_prompt_estimate_tokens` is the separate estimate for the request
  about to be sent. It is not current history usage and is not the model limit.
- `token_gate_prompt_tokens` is the latest sample eligible for the token gate
  evaluated at `LLM_REQUEST_POST`: a positive `_get_prompt_tokens()` value for
  the history just sent, or (only when that value is non-positive) the
  current-history estimate as fallback. Preserve whether the selected value is
  reported or estimated. The token-gate policy is unchanged: a positive
  `_get_prompt_tokens()` value is used even if its provenance is marked
  estimated; only a non-positive value falls back to `_estimate_history_tokens()`.
  Do not describe an unevaluated history estimate as the gate merely because it
  is current.
- Immediately after compaction/history replacement and before the next
  `LLM_REQUEST_POST`, the previous `last_request_prompt_tokens` is stale
  historical data. `current_history_prompt_estimate_tokens` remains a
  current-history estimate with estimated provenance, but
  `token_gate_prompt_tokens` and
  `tokens_until_compaction_gate` are unknown because no post-swap gate sample
  exists. `_record_prompt_token_scale()` clears the stale marker before
  `_should_compact()` evaluates the next completed request. At that evaluation,
  a positive `_get_prompt_tokens()` value is used even if its provenance is
  estimated; only a non-positive value falls back to the current-history
  estimate. Keep the stale historical count out of the gate and distance fields.
- `tokens_until_compaction_gate` is `max(0, threshold - token_gate_count)` only
  when both inputs are known and the gate sample is current. It is distance to
  the token gate, not a promise that compaction will run. The independent
  minimum-human-turn gate, paused/no-shrink guard, and other policy conditions
  may prevent compaction even at zero distance. A zero distance never changes
  `status` by itself.
- `model_context_window_tokens`, `automatic_compaction_threshold_tokens`, and
  `hard_cap_tokens` are separate. The current policy can use a 272,000-token
  cap for a 1,000,000-token context window and a known 100,000-token fallback
  when the model window is unknown. A known fallback threshold must not cause an
  unknown model window to be displayed as known.
- `threshold_source` is `manual` only when a positive configured manual token
  threshold actually lowers the automatic policy ceiling after ratio clamping
  and the global cap. If the configured value is equal to or above that ceiling,
  it is non-binding: report `automatic` for a known model window or `fallback`
  for an unknown window. A manual value that lowers the fallback ceiling is
  `manual`; use `unknown` if its effective source cannot be established. In ACP
  mode the native source is `not_applicable`.
- Native `status` values are the plugin's actual state vocabulary:
  `idle`, `compacting`, `ready_to_retry`, `applied`, `failed`, `paused`,
  `warning`, and `rolled_over`. `unknown`, `unavailable`, and `not_applicable`
  are API source/mode states, not plugin states. The UI must handle every value
  explicitly; an unknown value must not silently become `failed`.
- `automatic_compaction_enabled` reports effective per-session policy, not the
  global configuration flag alone. It is false when automatic compaction is
  disabled in configuration or disabled for this session after repeated
  failures. When false, `automatic_compaction_disabled_reason` reports
  `configuration` or `session_failures`; when enabled, that reason is
  not-applicable. A missing/failed policy source remains unknown. This policy
  state is separate from `status`: a last `failed` or `paused` action must not be
  relabeled healthy/idle merely because the global plugin setting is enabled.
- `mcp.state` meanings are mutually exclusive: `off` means the selected daemon
  confirms MCP is disabled or unconfigured; `error` means the MCP source/provider
  failed; `zero_connected` means MCP is configured and readable but no server is
  connected; `connected` means at least one server is connected; `unavailable`
  means no selected-daemon MCP source exists. A connected-server count of zero
  is a measurement only when its producer confirms it; missing or failed MCP
  data is not a zero. Do not collapse these states based on totals alone.
- `runtime.daemon_voice.stage` is a `Metric` whose value is one of `off`,
  `starting`, `listening`, `hearing`, `transcribing`, `speaking`, `enrolling`,
  `voiceprint_saved`, or `error`. Enrollment progress strings map to `enrolling`;
  the completed enrollment phrase maps to `voiceprint_saved`. Do not forward raw
  stage text, transcript text, or microphone-level payload. `off` requires a
  successful report that voice is disabled; a missing plugin is null with
  freshness `unavailable`. A failed lookup sets `source_health.voice` to
  `error`; requested/running/stage remain unknown (or a stale last-good sample),
  never synthetic false/off. `source_health.voice` is independent of broader
  system-info health.
- A processing-state read failure sets `source_health.processing` to `error`.
  `runtime.is_processing` remains unknown if never sampled, or stale if a
  last-good value is retained; it must never be caught and represented as
  current `false`/Ready.

- `message_code` and `action_code` are finite, allowlisted keys mapped to safe
  UI copy/actions. Never expose arbitrary plugin message/action strings or raw
  exceptions. Include at least safe mappings for retry, manual compact, continue
  next request, start a new session, reduce history, inspect logs, and continue
  recovery summary as applicable.
- In ACP mode, native policy/context measurements are `not_applicable` unless
  the active ACP harness itself reports a corresponding value. `acp.context_used`
  and `acp.context_limit` are harness-reported and are not renamed to native
  prompt tokens/model limits. Currency is present only when the harness reports
  it; do not infer USD. Native compaction thresholds are not invented for ACP.
- `last_request_input_tokens` is the input/context size of the last provider
  request, not a sum of continuation requests; its metric freshness determines
  whether it is current or historical.
- Output, cache, cost, and thinking-duration values written when a provider turn
  finalizes are `last_completed_turn_*`, never `current_turn_*`. The producer
  assigns a stable `turn_id` and atomically publishes that ID with the finalized
  values. Every such metric belongs to `last_completed_turn_id`. When turn B is
  active after turn A completed, retain A's values only under the explicitly
  named last-completed fields and ID; do not present them as B's current stats.
  Current-turn values remain unknown until a source reports values tagged with
  B's authoritative turn ID. If no authoritative turn ID accompanies a value,
  keep its metric unknown rather than associating it with the active turn.
- Cumulative counters include only their actual authoritative portions; mark
  them partial when estimated or omitted portions are excluded. Cache-read
  tokens are a separate metric and must not be added to input tokens a second
  time.
- A missing cache field is unknown, not zero. Costs with unknown pricing are
  unpriced (`amount: null`), not `$0.00`. Estimated or partial costs carry that
  state. Native costs use the declared currency; ACP costs use only the currency
  supplied by the harness.
- ACP usage is scoped to the current conversation and current daemon generation.
  On native/ACP mode changes, new/resumed conversations, or daemon replacement,
  do not retain the previous mode's model, counters, cost, or status. Restored
  values are shown only with restored provenance; otherwise they are unknown.

### Source freshness and ordering

- `source_health` is maintained independently for each source. `fresh` means a
  successful sample within that source's declared freshness interval; `stale`
  means a prior sample is being retained beyond that interval; `unavailable`
  means no producer exists or the source is disabled; `error` means a safe,
  recognized source failure. One source failure leaves other source values
  available. A source failure never publishes its exception text.
- Each `Metric` independently records value-level `freshness` and the original
  `observed_at`. `SourceHealth.observed_at` is the latest collection-attempt
  time, not the value sample time. Thus a failed refresh can produce source
  health `error` while retaining a value marked `stale`; a newer successful
  estimate can be `fresh` in the same snapshot. Freshness must not be inferred
  from the snapshot assembly time.
- The engine session-status assembler owns `attachment_epoch`, a monotonically
  increasing per-`engine_session_id` counter. Under one session-state lock, it
  increments the epoch before publishing any snapshot/event when either
  `conversation_id` or `daemon_generation` changes. Persist/restore the counter
  across engine attach/adoption/restart. If the prior counter cannot be restored,
  do not reuse its `(engine_session_id, attachment_epoch)` identity; establish
  an authoritative new session/incarnation identity before serving status.
- The identity tuple is
  `(engine_session_id, attachment_epoch, conversation_id, daemon_generation)`.
  `revision` is ordering metadata, not identity; it increases monotonically
  within an attachment epoch, including source-freshness changes. The assembler
  owns epoch transitions, revisions, and actual sample timestamps.
- For initial-load/live-update reconciliation, subscribe and fetch a complete
  snapshot. Reject a lower attachment epoch before comparing revisions. Within
  the active epoch, accept only matching identity fields and a revision newer
  than the current snapshot; ignore duplicates and older revisions. A higher
  epoch is installed only by a complete snapshot/resync or an explicit identity
  transition event that carries a complete replacement snapshot; never merge
  incremental values across epochs. A mismatched event requests a full resync.
  This rejects a delayed A response/event even if its old revision is numerically
  larger than the first revision from B.
- Required ordering regression: apply A at epoch 7/revision 120, then B at epoch
  8/revision 2, then deliver a delayed A GET and A SSE event at epoch 7/revision
  999. Both A payloads must be ignored and B must remain displayed. Also cover
  conversation changes within one daemon generation and daemon replacement
  without a conversation change.
- A revision gap, invalidation marker, stream close caused by event loss, or
  reattach to a new generation requires a full snapshot fetch before rendering
  further incremental values. The current SSE fan-out can drop events when a
  subscriber queue is full; implementation must turn that into a detectable
  invalidation/close and resync, not a silent drop. An explicit overflow signal
  is required so a dropped final update is detectable even if no later event
  arrives to reveal a revision gap.
- Do not expose updates from another session, conversation, daemon, or device.
  A late event from a previous attachment epoch is ignored regardless of its
  revision.

## Privacy, authorization, and device scope

- The current engine auth middleware validates a process-wide bearer token,
  and the state route then looks up the registry. Neither step establishes
  per-session read authority. Construct a server-side `SessionReadContext` only
  after authenticating the caller; it carries a server-derived principal,
  `access_path`, and, when applicable, trusted peer/device and trust-generation
  identifiers. Attach this context to trusted request/connection scope. Never
  derive it from a client-supplied principal/access-path header, query value,
  body field, requested `engine_session_id`, `agent@device` display name, or
  PhoneSession relay ID. If current Web UI authentication cannot distinguish a
  caller, add a server-minted authenticated UI-session context; the shared bearer
  alone is insufficient.
- The engine-owned session ownership/share policy is the sole source of
  `SessionReadGrant` records. Issue an opaque, unforgeable, server-validated
  grant only for sessions that the principal may read. Bind each grant to the
  principal, `access_path`, exact `engine_session_id`, target device for remote
  access, `attachment_epoch`, expiry, and revocation/version state. A session ID
  in a route is only a target selector, never authority. A client may present a
  grant but cannot mint it, choose its scope, or widen it. Authenticated session
  list/create/attach flows return grants only for sessions the caller may
  select; registry visibility or a successful attach is not an ACL.
- Web UI switching from A to B first obtains or validates B's grant under the
  same authenticated principal; it must not install B as `activeId` or request
  B's state until that grant is confirmed. A's grant cannot read B, and C with
  no grant is denied. An `attachment_epoch` change invalidates the old grant;
  the server may issue a replacement only after rechecking the ownership/share
  policy, and the client must resync from a complete authorized snapshot rather
  than merge data across epochs.
- PhoneLink derives its principal from the authenticated paired peer key plus
  the active, verified `PhoneSession`; the host injects this server-owned context
  into the ASGI scope after verification, not from forwarded client headers.
  `PhoneSession.host_session` and `phone_session` are transport correlation IDs,
  not engine ACLs. Issue grants bound to that phone principal and the exact
  engine session. Phone pairing revocation invalidates affected grants.
- Remote access derives its principal from the authenticated peer identity and
  current trust state. The `agent@device` name and destination `network_attach`
  admission are routing/admission checks, not read authorization. A local UI
  reading a remote session retains its local caller principal and also requires
  target-device trust plus a grant bound to that remote engine session.
- Before returning a GET, opening SSE, and enqueuing/delivering each event,
  revalidate the context and grant against current ownership, attachment epoch,
  expiry, and phone/remote trust. Grant revocation, expiry, attachment change,
  phone revoke, or remote trust revoke denies subsequent reads, purges queued
  status events, and closes the active stream before any post-revocation event
  can be delivered. A previous list or attach does not confer lasting authority.
- Required security tests: authorized A then B navigation succeeds only after
  B's grant arrives; A's grant cannot read B; unauthorized C and spoofed
  principal/access-path headers are denied; a shared bearer alone cannot read
  an unauthorized session; expiry/revocation and attachment-epoch changes
  invalidate grants; PhoneLink wrong-binding/revoked-pair reads fail; remote
  trust revocation denies GET and terminates SSE with queued events.
- The privacy allowlist in this section applies specifically to the new
  `runtime_status` object and its `session_runtime_status` SSE events; it does
  not claim that the entire legacy `/state` response is transcript-free. Today
  `system.voice_activity.heard` is returned by `get_system_info()` and consumed
  by `voice-mode.tsx`. Preserve that existing field/consumer in #473, subject to
  the same per-session authorization check, but never copy it into
  `runtime_status` or its SSE event. Treat the legacy field as sensitive and
  outside the new sidebar contract. Any later whole-response redaction must
  first migrate the existing consumer to a separately authorized source.
- Within `runtime_status`, never return prompts, transcript text, tool
  arguments, raw tool payloads, exception text, stack traces, API keys, provider
  credentials, MCP server secrets, or remote roster contents. Do not forward a
  whole `voice_activity`, ACP usage, plugin, or exception dictionary.
- `daemon_voice` describes only the selected daemon's voice plugin using
  `requested`, `running`, and the normalized stage enum. It does not describe or
  activate the viewing browser's microphone. Exclude transcript and mic-level
  data.
- `cwd` and `git_branch` come from the selected daemon's `get_system_info()`;
  do not run a host-side Git command or use a global cache. The two-device test
  must prove different selected-daemon workspaces remain isolated.
- `active_skills` contains names only, not skill bodies. Hub status contains
  identity/coordinator/peer count only, not the full roster.

## UI behavior and accessibility

- Compact summary sits below the existing model row and shows effective model,
  token-gate distance, and the most useful cache/usage summary without pushing
  the conversation out of view. Expanded details use a keyboard-operable
  disclosure with clear labels and visible focus.
- Keep context window, token threshold, hard cap, and cache-read tokens distinct.
  Identify reported vs estimated values and turn vs cumulative scope.
- The existing compaction banner and the persistent sidebar use the same status
  vocabulary. `warning`, `rolled_over`, `ready_to_retry`, and `applied` are not
  coerced to failure. Show actual status separately from distance-to-gate.
- Unknown, unavailable, stale, error, and not-applicable states have distinct,
  concise user-facing treatments. A disabled plugin is visibly unavailable, not
  a zero or a healthy/idle state.
- The sidebar is read-only in #473; no Model/Effort selector or reset action.
- Validate responsive behavior at 390, 820, and 1280 CSS pixels, keyboard
  disclosure/focus, session switching, reconnect, stale-source and error states.

## Acceptance and review gates

Contract review tests and implementation must cover:

1. Widget inventory: all supported unique default-layout widgets appear once;
   duplicate MCP is rendered once; git status and deep-thought remain explicitly
   excluded until a selected-daemon producer exists; session display name follows
   the TUI rule.
2. Context policy: 1,000,000-token model window with 272,000-token policy
   cap; unknown model window with known 100,000-token fallback; binding and
   non-binding manual thresholds with correct automatic/fallback source;
   last-request, current-history, and outgoing estimates remain distinct. After
   history swap, a stale `last_request_prompt_tokens` may coexist with a fresh
   history estimate, but gate and distance are unknown until the next
   `LLM_REQUEST_POST`. At that evaluation, a positive `_get_prompt_tokens()`
   value wins (preserving its reported/estimated provenance); only a
   non-positive value falls back to the history estimate, matching the unchanged
   `_should_compact()` policy. Cover one human turn with older history,
   paused/no-shrink at zero distance, configuration-disabled and
   session-disabled-after-failures states, and no-op/failed compaction. Verify
   effective enablement and disabled reason separately from action status, and
   cover every status enum.
3. Usage: measured zero vs missing fields; omitted cache metrics; current input
   scope vs continuation-aggregated output/cache; partial cumulative totals;
   estimated usage provenance; unknown pricing and partial/unpriced costs; cache
   reads are not double-counted; completed turn A with nonzero output/cache/cost,
   then active turn B, proving A remains only under its `last_completed_turn_id`
   and B's current counters are unknown until B-tagged values arrive; publish B's
   id and finalized values atomically.
4. Provider mode: native -> ACP -> native, missing ACP usage, ACP model/window
   differing from saved profile, harness-provided currency, and no native
   thresholds inferred for ACP.
5. Lifetime/ordering: `/new`, `/resume`, late previous-conversation events,
   daemon restart/generation change, reordered GET/SSE data, duplicate revisions,
   the stale epoch-7/revision-999 A response/event after epoch-8/revision-2 B,
   dropped final update/queue overflow, reconnect/resync, session switching, and
   individual source failures. Verify epoch is identity and revision is ordering
   metadata only.
6. Security/device and degraded sources: authorized A then B navigation
   succeeds only after B's grant arrives; A's grant cannot read B; unauthorized
   C and spoofed principal/access-path headers are denied; a shared bearer alone
   cannot read an unauthorized session; grant expiry/revocation and attachment-
   epoch changes invalidate access. PhoneLink wrong-session/revoked-pair reads
   fail, and an already-attached remote session is denied after trust revocation.
   GET and SSE authorization must include revocation during an active stream with
   queued events. Also cover two selected daemons with different workspaces;
   daemon-vs-browser microphone scope; normalized voice
   starting/listening/hearing/transcribing/speaking/enrolling/voiceprint-saved/
   error states; voice and processing read failures remain error/unknown rather
   than false/off/Ready; actual `/state` test proves legacy
   `system.voice_activity` remains available to `voice-mode.tsx` while
   `runtime_status` and its SSE event contain no transcript, prompt, tool
   argument, secret, or raw error data.
7. UI: automated component/API tests; keyboard and responsive verification at
   390/820/1280; real browser proof on port 8080; focused suite and full required
   CI green.

Review gates: Sapphire's Round 1 widget inventory passed with corrections now
incorporated. Earlier six Round 2 findings (issuecomment-6541025886) are
incorporated. Nephrite's latest Round 2 re-review is BLOCK with two remaining
contract findings (issuecomment-6541217258). This revision addresses both and
requests a fresh verdict; Round 2 remains blocked until Nephrite explicitly
passes it. Rounds 1 and 2 gate Web UI work and the full runtime-status
snapshot/live-event integration. In particular, the required server-created
principal and per-session grant authorization are not present in the current
session-state route and must be implemented and tested before exposing the new
snapshot or stream. The already authorized typed context-compaction snapshot
DTO/RPC/initial-state plumbing may be completed and verified as the narrow
backend-only slice while re-review runs; it is not the full `runtime_status`
contract and must not be consumed by the UI. Do not implement the remaining
source aggregation or status UI until Round 2 passes. Rounds 3 and 4 review
implementation and acceptance/browser evidence before opening the draft PR.
Merge only after all four rounds and all required gates pass.
