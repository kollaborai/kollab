# WebUI daemon-owned message queue

Issue: #1366
Status: Queue state, authenticated cleanup RPC, injected worker, and attachment staging/atomic admission implemented; live daemon/provider/media and attachment RPC wiring remain open
Owner: Kollab daemon state / WebUI integration

## Problem

The WebUI currently keeps waiting messages in page-local assistant-ui state. A
reload loses them, another engine client cannot see them, and Stop can discard
messages that have not started. Native `QueueProcessor` and `ACPRunner` queues
do not provide this contract: their admission and cancellation semantics differ.

## Goal and non-goals

Give one Kollab agent daemon a bounded, ordered queue of WebUI message batches
that survives page and engine/session reloads while that daemon process remains
alive. Queue changes are atomic, idempotent, visible through a revisioned
snapshot, and can be claimed or removed without splitting a batch.

This slice includes the in-memory queue primitive, a bounded per-scope registry,
a JSON-safe handler registrar for the daemon's existing `RpcServer`, and a
serial worker primitive with an injected admission dispatcher. It does not yet
register those handlers or the worker in the Hub daemon lifecycle, add
`RemoteStateService` client methods, bridge queue-change events to the engine
session stream, change HTTP routes or the WebUI, or connect the injected
dispatcher to native queue admission, ACP steering, or provider execution.
Daemon-process restart persistence is not promised.

## What people see

The intended WebUI row shows `Queued` with `Send Now` and `Remove` actions. A
reloaded page or second engine client reads the daemon snapshot. A Send Now
batch moves ahead of older queued batches as one envelope while preserving its
internal message order. An item that is already claimed cannot be removed.
An ambiguous provider steer is shown as in doubt and is never retried
automatically.

## Contract

The daemon owns one `WebMessageQueue` per canonical conversation and local
operator scope; an `EngineSession` is only a client. On POSIX, the RPC transport
derives the operator principal from the accepted Unix peer UID. On Windows, it
uses the local IPC endpoint-key check and identifies the principal as the local
owner. Off-box RPC requests do not receive operator context and are rejected by
the existing admin gate. This trust domain does not distinguish browser or
engine clients running under the same OS account, and the Engine's shared bearer
token is not a per-user identity. The caller must construct the primitive with
that opaque `scope_id` and must not share it across conversation or authorization
boundaries. All clients admitted to the same scope observe the same queue. The
primitive stores opaque `bytes` payloads, never parses them, and excludes
payloads from reprs and error messages. It does not log message content.

Each scoped queue mints stable `operation_id`, `batch_id`, and `request_id`
tokens for its current queue incarnation. A queue instance remains stable while
retained by the daemon registry; idle empty queues may be retired only after
their full retry lease expires. Each token has the form
`kind-incarnation-monotonic-nanoseconds-nonce-mac`; all fields use letters,
digits, `_`, or `-`, so `request_id` passes the WebUI transport's client-ID
predicate `_CLIENT_REQUEST_ID` in
`packages/kollabor-engine/src/kollabor_engine/routes/messages.py` unchanged.
The 128-bit truncated HMAC-SHA256 authenticates the token
to this queue incarnation and rejects altered or fabricated handles. Clients
retain each ID and reuse the exact operation ID for retries. Replaying the same
operation and payload before the retry lease expires returns its receipt
without a second mutation. Reusing an operation ID with different arguments is
a conflict. An operation or newly admitted entity ID at or past the 15-minute
default retry lease is rejected as stale; it is never reinterpreted as new
work. A stale response requires snapshot reconciliation before the client
starts a distinct operation. A claim replay keeps the same claim ID but reports
its current status and revision; it never returns payloads.

Queue IDs are idempotency handles, not authorization. A valid MAC only proves
that the current daemon queue minted the handle; each RPC must still enforce
the authorized principal and canonical conversation scope. The signing key is
process-local and discarded at daemon restart.
Enqueue validates the whole batch before committing it; no prefix can be
accepted. A batch envelope preserves item order and receives one queue sequence.

`WebMessageQueueRegistry` bounds the number of retained conversation/principal
queues (default 16). When full, it may retire a scope only if that queue is
empty, has no active or in-doubt claim, all operation receipts and entity
tombstones are beyond their retry leases, the latest ID it minted is past its
retry lease (or the queue has minted no IDs), and no handler holds a registry
lease. Otherwise a new scope is rejected with `QueueScopeCapacityExceeded`; no
pending work is evicted. At the defaults, the registry bounds pending payload
capacity to 128 MiB across all scopes, plus bounded IDs, receipts, and Python
object overhead.

`register_web_message_queue_handlers(rpc_server, registry, resolve_scope=...)`
adds these JSON RPC methods:

- `state.web_queue.allocate_ids` returns operation IDs, one batch ID, and the
  requested item IDs before enqueue.
- `state.web_queue.enqueue_batch` takes opaque payloads as base64 and commits
  the full ordered envelope atomically.
- `state.web_queue.snapshot` returns progress and base64 payloads for authorized
  display/reconciliation.
- `state.web_queue.claim_next`, `claim_batch`, `claim_next_item`,
  `item_payload`, `settle_item`, `remove_item`, and `release_claim` mirror the
  state primitive's claim and mutation contracts.

The required `resolve_scope(params, caller_context)` callback returns an opaque
canonical scope derived from trusted transport context and daemon conversation
identity. The standard `daemon_conversation_scope_resolver` returns a trusted
`QueueScopeBinding(scope_id, conversation_uid)` so the registry can retain the
principal and canonical UID association needed for stale cleanup. A custom
resolver that returns only an opaque string can authorize current-scope calls,
but does not enable old-scope cleanup. `AgentSocketServer` constructs the
immutable `RpcCallerContext` only after the local operator gate succeeds; it is
passed in-process by `RpcServer` and never decoded from the RPC frame. Queue handlers reject missing or
non-local context before resolving scope. `daemon_conversation_scope_resolver`
compares the required `expected_conversation_uid` against the daemon's
`StateService.get_conversation_uid()` and derives scope from that backend value
and the authenticated operator principal. The request value is only a
compare-and-swap precondition: `scope_id`, `session_id`, and `principal_id` in
parameters never select scope. `conversation_uid` is used because
`current_session_id` rotates on resume. A missing backend or expected identity
fails closed. A well-formed UID mismatch is classified internally as stale only
after the daemon returns a valid current UID; missing context, resolver failure,
and unavailable state do not enter the stale path. Each queue retained from a
successful resolution is bound in the registry to that authenticated principal
and conversation UID. Scope capture is synchronous before queue locks can yield;
a request stays in the captured scope if a conversation switch races after that
point.

After a switch, a stale-scope request may use only `settle_item` or
`release_claim`, and only with both the pre-issued operation token and the
matching queue-issued attempt/claim token. The adapter searches only existing
registry entries bound to the same principal and the exact expected old UID;
the search is bounded by the registry scope cap and never creates a queue. It
checks both token MACs and retry leases, and verifies the claim is active or the
same cleanup operation is a valid replay, before calling the queue mutator.
Candidate probing is read-only. A missing, expired, cross-principal,
cross-conversation, wrong-incarnation, ambiguous, or otherwise invalid pair
gets one generic authorization failure and emits no change hint. Snapshot,
`item_payload`, ID allocation, enqueue, claim, and removal still fail closed for
a stale UID. This prevents an old operation from being redirected into the
newly selected scope and prevents old-scope reads or new work. Integration must
still verify the active conversation again at provider admission so an already
claimed old-scope item cannot be steered into a newly selected conversation.

A trusted daemon worker that retained the direct queue and attempt when it
dispatched may call the internal `settle_item_from_worker(attempt_id, outcome)`
after the caller's settlement operation-token lease expires. This method is not
registered as RPC and exposes no stale-scope allocator, snapshot, or payload
path. Under the queue lock it authenticates the attempt, requires the exact
active attempt, and mints a fresh operation ID only after its reserved
settlement slot is available. The worker must supply definitive provider
evidence for `accepted` or `not_sent`; ambiguous evidence remains `in_doubt`.
The receipt is pinned in the existing bounded operation ledger at the
lock-protected settlement commit. The worker validates the exact receipt and
acknowledges it through an in-process method. Until then, the receipt survives
its retry lease, only one such receipt may be pinned per queue, and new claims
are blocked. Conflicting outcomes cannot replace the pinned outcome. The pin
uses the lifecycle receipt slot reserved when the batch was claimed. One
fixed-size last-acknowledgement tombstone makes an exact repeated
acknowledgement idempotent. Neither record retains payload bytes or grows with
queue history.

The same-UID operator boundary does not provide per-client authorization. The
optional `on_change` must be the built-in `put_nowait`
method of a finite `asyncio.Queue`; arbitrary or async callbacks are rejected.
The adapter reads
the current queue revision for invalidation metadata, so replayed operations
cannot publish an older receipt revision. Queue-full hints are dropped. There
are no detached notifier tasks and notification delivery is not guaranteed.
Snapshot RPC is authoritative; event-driven integration remains blocked until
consumers have a coalescing/reconciliation contract that recovers from dropped
hints. Polling or explicit snapshot reconciliation is required meanwhile. The
event metadata never includes payloads.

The snapshot is `{scope_id, incarnation, revision, batches}`. Each batch has
an ordered list of remaining items; each item reports `queued`, `dispatching`,
or `in_doubt`. Accepted items are removed from pending state, so a confirmed
prefix cannot be replayed. Receipts also carry `scope_id`. The process-local
`incarnation` identifies the scoped queue instance and changes if an expired
idle queue is retired or the daemon restarts; `revision` increments once per
committed visible mutation and starts at zero in a new incarnation. Pages
may receive revision hints, then reconcile snapshots by incarnation and
revision.

`snapshot()` is for authorized UI display and reconciliation only. Its items
include opaque payload bytes, including bytes for an `in_doubt` item, so the UI
can render the original message and its status. A snapshot is never a provider
dispatch source. Provider input may come only from `item_payload(attempt_id)`
while that attempt is currently dispatching; the method refuses in-doubt or
otherwise inactive attempts. This primitive does not authenticate RPC callers
or enforce roles. Every RPC, including snapshot and `item_payload`, must enforce
the authorized principal and canonical conversation scope before reading data.

`claim_next` atomically claims the oldest eligible envelope. `claim_batch(...,
send_now=True)` atomically claims the named queued envelope and moves it ahead
of older envelopes; one active batch claim prevents interleaving. Then
`claim_next_item(operation_id, claim_id)` claims exactly the next unaccepted
item. The next item cannot be claimed until the current item is settled.
`item_payload(attempt_id)` returns only that one item and only while its
attempt is dispatching. Operation receipts retain no message payload bytes.
Replaying a batch or item claim returns the same IDs and current lifecycle
status, so an in-doubt replay cannot look like fresh work. `settle_item` has
three outcomes:

- `accepted`: remove only that item. If suffix items remain, keep the envelope
  reserved and continue at the next item. Remove the envelope after its last
  item is accepted.
- `not_sent`: release the current item and remaining suffix to queued state in
  their original order. Any already accepted prefix stays removed.
- `in_doubt`: mark only the current item in doubt and block later items and
  envelopes. The suffix remains queued in the same envelope. Only an explicit
  later resolution may mark the uncertain item accepted or definitely not
  sent. Never retry an in-doubt item automatically.

Removal targets one `request_id` and succeeds only while its envelope is
queued. Removing the last item removes the envelope. Claim/remove races are
serialized: exactly one operation wins. Stop changes active provider work; it
does not mutate this queue.

## Injected daemon worker boundary

`WebMessageQueueWorker` is a process-local primitive, not a registered daemon
service. Its caller supplies a trusted `ConversationIdentity(uid, revision)`, a
current-identity reader, and an async dispatcher. The worker checks that
identity before claiming, before each item, and again after reading that item's
payload. Those checks are preflight guards only. The injected dispatcher must
atomically revalidate the exact captured UID and revision at provider admission;
a conversation switch between the worker's check and provider admission must
be rejected before side effects.

The worker claims one batch and then serially claims one item, reads its bytes
only through `item_payload(attempt_id)`, and calls the dispatcher with the
unchanged `request_id`, captured UID/revision, and queue scope/incarnation. It
never dispatches from a snapshot. A typed `AdmissionAccepted` must echo the
exact UID, revision, and request ID and include a custody ID. The queue item is
then settled accepted. This means downstream admission custody only; it does
not mean model completion. `recording_status` is carried separately because
ACP steering may be accepted before conversation-history recording succeeds or
its outcome is known. A future native dispatcher may return accepted only
after the local `QueueProcessor` commit reports `queued`; an ACP queued result
requires `runner.put_nowait` custody, while an ACP injected result requires a
successful steering receipt. A history-recording exception after successful
steering and an `ACPSteeringUnconfirmed` result are ambiguous, not proven
pre-admission rejection.

A typed `AdmissionRejectedBeforeCustody` settles `not_sent` only when it echoes
that same identity and request ID and asserts that no side effect occurred.
Errors before the dispatcher call, including a stale identity or unreadable
payload, are proven not sent and requeue the current item and suffix. Any
exception, cancellation, malformed/mismatched receipt, or untyped result after
the dispatcher call begins is ambiguous: the worker settles the current item
in doubt, stops, preserves the suffix, and never retries automatically. A
proven rejection also stops the run after requeueing current plus suffix. Each
accepted item is settled before the next item is claimed, so the queue retains
only the unaccepted suffix if the run stops partway through.

The worker reports `admitted` only after every item in that run has a matching
custody receipt. That status does not mean generation completed or history was
recorded; consult each item's `recording_status` for the separate history
outcome.

`request_stop()` prevents the next item from starting. It is a worker-local
batch boundary and is not wired to the product Stop action. Product Stop
integration must remove the exact request before item claim, release a claimed
but pre-provider item as not sent, and cancel only the exact accepted request
after custody while retaining its accepted queue state. Existing native
`cancel_current_request` is global and sweeps queued `TaggedInput` values, so it
must not be treated as item-scoped cancellation. An active injected dispatcher
determines its own terminal outcome; this worker does not cancel it for Stop.
Native terminal events must correlate `request_id` and `request_ids[]`, since
several accepted inputs may share one provider turn.

`close(timeout)` stops new runs and waits within the specified bound. If the
bound expires, it retries an already recorded settlement intent; without one,
it settles a pre-dispatch item as `not_sent` or an invoked dispatch as
`in_doubt`, then cancels the worker task. `close()` returns false
if worker termination or terminal queue state was not confirmed; the daemon
must not register a replacement worker over that attempt, and must keep the
provider alive while the task can still call it. The worker does not own
provider shutdown.

Settlement uses the queue's in-process `settle_item_from_worker` recovery path,
which is reserved for the active attempt and remains available after the claim
operation token's retry lease expires. This recovery is not an RPC. No provider
adapter, Hub registration, runtime lifecycle, persistence, or live admission
behavior is implemented or verified by this primitive.

Before a batch claim becomes visible, the queue reserves capacity for its claim
receipt, one pre-dispatch release receipt, and each item's claim, settlement,
and possible in-doubt-resolution receipts. These reservations are atomic under
the queue lock. If the complete lifecycle cannot fit, claim rejects while the
batch is still queued. A claim can be explicitly released only before the first
item claim; release is idempotent. After item dispatch starts, only item
settlement can release the batch. Reservation slots are consumed by receipts
or freed when the corresponding lifecycle ends.

Default bounds are 100 pending items, 20 items per batch, 1 MiB per opaque
payload, 8 MiB total pending payload, 4096 live operation receipts, and a
15-minute retry lease. Receipts are retained through their lease and then
pruned; active claim and item-claim receipts stay pinned until their claim is
released or settled, and one worker settlement receipt stays pinned until the
worker acknowledges it. A single fixed-size acknowledgement tombstone supports
repeating the most recent worker acknowledgement. Expired operation IDs return
`StaleOperation`. Batch and
item ownership tombstones are retained through the entity ID's retry lease;
pending and in-doubt entities stay tracked regardless of age. Expired entity
IDs return `StaleIdentifier`, so pruning a tombstone never makes an old ID
reusable. The combined owner map is hard-capped by `max_entity_ids`; its default
is `2 * max_operations * (max_batch_items + 1) + 2 * max_items` (172,232 IDs
with the defaults above). If it fills before tombstones expire, enqueue rejects
with `IdentifierLedgerFull` and no mutation. Expired tombstones are pruned on
the next mutation attempt. Capacity exhaustion is explicit; integrating code
must surface it and must not log payloads.

## Scenarios

- If two engine clients subscribe to one daemon and either queues a batch, then
  a snapshot with the same incarnation and a newer revision shows that batch to
  both clients.
- If the page or engine restarts while the same daemon remains alive, then the
  queue snapshot still contains the pending batch.
- If the daemon process restarts, then its in-memory queue is empty and has a
  new incarnation; no persistence claim is made.
- If a scoped queue is empty and has no unexpired receipts, tombstones, minted
  IDs, or active registry leases, then the daemon may retire it under registry
  pressure; later access gets a new queue incarnation and old IDs cannot mutate
  it.
- If registry scope capacity is full and no scope satisfies every retirement
  condition, then a new scope receives explicit bounded rejection and retained
  queue state remains intact.
- If a retried enqueue uses the same operation ID and payload, then the same
  receipt is returned and the batch appears once.
- If an operation or entity retry lease expires, then the old ID is explicitly
  stale, its receipt/tombstone may be pruned, and retrying that ID cannot create
  a second batch or item.
- If any item in a batch is invalid or a capacity bound would be exceeded,
  then none of its items are committed.
- If Send Now claims a batch while older batches wait, then it is one claimed
  envelope ahead of them, and no other batch can be claimed until it settles.
- If item 1 is accepted, item 2 becomes in doubt, and item 3 was not sent, then
  the snapshot contains items 2 and 3 with distinct statuses; item 1 is gone.
- If an in-doubt item is resolved as accepted, then only that item is removed
  and the suffix remains available in order under its existing reservation.
- If an in-doubt item is resolved as not sent, then that item and its suffix
  return to the queue in original order; accepted prefix items stay removed.
- If a Send Now result is ambiguous, then the in-doubt item's payload is not
  available to a retrying dispatcher and later work waits for reconciliation.
- If removal races with claim, then either removal succeeds and claim cannot
  find the item, or claim succeeds and removal reports that it is no longer
  queued.
- If cancellation arrives after batch claim but before the first item claim,
  then explicit idempotent `release_claim` returns the envelope to queued state.
- If cancellation arrives after an item claim, then `release_claim` fails and
  reserved item settlement remains available.
- If the daemon conversation changes after a queue claim, then the same local
  principal may settle the active item or release a pre-dispatch claim with its
  pre-issued operation and claim/attempt tokens. The stale scope cannot provide
  payloads, snapshots, new IDs, claims, or enqueues; a token from another
  principal, conversation, queue incarnation, or expired lease is denied
  without mutation, notification, or scope-specific error detail.
- If a trusted daemon worker resolves an active attempt after its operation
  token lease expires, then the internal worker path mints a fresh bounded
  settlement receipt under the queue lock; it cannot be called through stale
  RPC, and ambiguous evidence keeps the item in doubt.
- If a worker commits a settlement but loses the receipt acknowledgement, then
  the exact outcome remains pinned beyond the retry lease until worker
  validation; competing claims and conflicting outcomes are rejected meanwhile.
- If ledger capacity cannot cover a whole claim lifecycle, then the claim fails
  before dispatch; a later unrelated write cannot consume reserved settlement
  or release capacity.

## Failure modes

- Duplicate operation ID with changed arguments: conflict; no mutation.
- Duplicate batch or request ID under a different operation: conflict; no
  mutation.
- Queue, payload, receipt, or entity-ID capacity exceeded: explicit bounded
  rejection; no partial enqueue or claim.
- An operation or new entity ID is stale: reject it explicitly and require
  snapshot reconciliation; never treat it as a fresh ID.
- A token has the wrong incarnation or an invalid MAC: reject it as an invalid
  identifier before any mutation; the token does not bypass RPC authorization.
- A cleanup request uses a stale UID but has no unique existing scope for the
  authenticated principal and exact old UID, or its operation/claim tokens do
  not authenticate and match the active cleanup: return the same generic
  authorization failure without probing through mutating queue calls.
- A worker recovery uses a wrong-incarnation, nonactive, or conflicting attempt:
  reject without mutation; do not expose the internal method as an RPC handler.
- Claim already active: explicit busy result; no second envelope is claimed.
- Queue claim attempted while an item is in doubt: explicit `in_doubt` result;
  no item or envelope is claimed.
- Missing or non-queued removal target: explicit not-found/already-claimed
  result; other items remain unchanged.
- Provider confirms an item was not admitted: release that item and the suffix
  to queued state, preserving accepted prefix progress.
- Provider result for one item is ambiguous: mark that item `in_doubt`, block
  later items, and do not retry it.
- A daemon restarts: pending memory state is lost and the incarnation changes.

## Decisions

- Queue ownership is in the agent daemon, not `EngineSession` or provider
  queues, because clients must share state and page reload must not erase it.
- A batch is one queue envelope so multiple sends cannot be interleaved by
  another queue claim.
- Pending content is opaque, private bytes with explicit per-item and total
  bounds. This primitive does not decide how image references are staged.
- The queue and replay receipts last only for one daemon process lifetime.
  Durable restart recovery needs a separate storage contract.
- An ambiguous ACP steering result is not safe to retry. It blocks subsequent
  claims until integration code reconciles it against provider evidence.
- Operation and entity IDs are queue-minted per-incarnation tokens with a
  bounded retry lease. The daemon can prune expired receipts and tombstones
  without treating an old retry as new input.
- Queue tokens use a per-incarnation stateless MAC. This authenticates issuance
  and integrity only; principal and conversation authorization remain on each
  daemon RPC.
- An active claim reserves its complete release/dispatch/settlement lifecycle
  before becoming visible. Capacity exhaustion therefore rejects the claim
  before any provider command can be sent.

## Test plan

Red-first unit tests cover batch order, all-or-none capacity rejection,
idempotent replay and payload conflict, private payload reprs, revision and
incarnation behavior, atomic Send Now promotion/claim, per-item accepted
prefixes, in-doubt middle items, recoverable suffixes under both explicit
resolution outcomes, removal/claim races, pre-dispatch claim release, concurrent
release/item-claim winner selection, reserved lifecycle capacity, retry-lease
expiration, lost and cancelled settlement acknowledgements beyond lease,
conflicting outcomes, repeated acknowledgements, bounded receipt/tombstone
pruning, and stale-ID rejection.
RPC adapter tests cover JSON round-trips, trusted scope resolution, refusal of
caller-selected scope IDs, scope rebinding across principals, same-principal
stale-scope settle/release with pre-issued tokens, and refusal of stale
payload/snapshot/new-work access, cross-principal or wrong-incarnation
capabilities, and mismatched-candidate probes without mutation or notification.
They also cover registry contention,
immediate retirement of snapshot-only scopes, lease-safe retirement after IDs
are issued, current revision hints on replayed enqueue/remove/settle/release,
rejection of async and unbounded notifier callbacks, and bounded overflow with
snapshot reconciliation. Queue tests bound consumption of oversized item
iterables to the configured batch limit plus one lookahead.
The primitive test also confirms an authorized-display snapshot retains an
in-doubt item's content and status while `item_payload` and the next-item claim
refuse to retry it. The integration PR must test that each RPC rejects
unauthorized principals and cross-conversation access, and that provider input
is sourced only through `item_payload` for the active dispatching attempt. The
primitive itself does not enforce caller roles. Live proof is not part of this
primitive-only change.

## Docs and changelog fragment

This file is the canonical contract for the state primitive. A changelog
fragment belongs with the later integration PR that makes the feature visible
to users.

## Open questions

- Integration must sequence native and ACP admissions through one batch claim,
  mint queue IDs before admission, preserve each exact operation ID through
  RPC retries within its lease, and reconcile snapshots when a retry returns
  stale. Backend-specific multi-message steering semantics remain unresolved.
- Integration must register the handlers and registry once for the daemon
  lifetime, provide a trusted per-principal/canonical-conversation scope
  resolver, add remote client methods, and surface explicit scope-capacity
  rejection.
- Integration must publish revision-only queue invalidations to the current
  session event stream and have the WebUI reconcile via `snapshot`; that path is
  not wired by this slice.
- Integration must define how provider evidence resolves an in-doubt claim.
- Integration must register the adapter and trusted resolver in the daemon
  lifecycle. The adapter enforces current-scope principal/conversation access
  and narrowly permits old-scope `settle_item`/`release_claim` after a validated
  conversation switch; the primitive alone still does not authenticate RPC
  callers.
- Integration must decide how queued attachments remain readable while waiting.
- This primitive exposes synchronous ID factories; the daemon RPC contract must
  make those IDs available to the WebUI before enqueue and mutation calls.
- A 15-minute retry lease is a state-primitive default, not a user-facing
  delivery guarantee. Integration must reconcile stale retries by snapshot and
  must not mint a new ID for an operation whose prior result is unknown.
