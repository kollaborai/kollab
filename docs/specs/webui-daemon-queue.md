# WebUI daemon-owned message queue

Issue: #1366  
Status: Implemented (state primitive only)  
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

This spec implements only the in-memory queue state primitive. It does not wire
RPCs, event fanout, HTTP routes, WebUI UI, native queue admission, ACP steering,
or provider execution. Daemon-process restart persistence is not promised.

## What people see

The intended WebUI row shows `Queued` with `Send Now` and `Remove` actions. A
reloaded page or second engine client reads the daemon snapshot. A Send Now
batch moves ahead of older queued batches as one envelope while preserving its
internal message order. An item that is already claimed cannot be removed.
An ambiguous provider steer is shown as in doubt and is never retried
automatically.

## Contract

The daemon owns one `WebMessageQueue` per canonical conversation and
authorized-principal scope; an `EngineSession` is only a client. The caller must
construct the primitive with that opaque `scope_id` and must not share it across
conversation or authorization boundaries. All clients admitted to the same
scope observe the same queue. The primitive stores opaque `bytes` payloads,
never parses them, and excludes payloads from reprs and error messages. It does
not log message content.

The queue mints stable `operation_id`, `batch_id`, and `request_id` tokens for
one daemon incarnation. Each token has the form
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

The snapshot is `{scope_id, incarnation, revision, batches}`. Each batch has
an ordered list of remaining items; each item reports `queued`, `dispatching`,
or `in_doubt`. Accepted items are removed from pending state, so a confirmed
prefix cannot be replayed. Receipts also carry `scope_id`. The process-local
`incarnation` is new for each daemon lifetime; `revision` increments once per
committed visible mutation and starts at zero in a new incarnation. Pages
subscribe to change events, then reconcile snapshots by incarnation and
revision.

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
released or settled. Expired operation IDs return `StaleOperation`. Batch and
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
expiration, and bounded receipt/tombstone pruning with stale-ID rejection.
Live proof is not part of this primitive-only change.

## Docs and changelog fragment

This file is the canonical contract for the state primitive. A changelog
fragment belongs with the later integration PR that makes the feature visible
to users.

## Open questions

- Integration must sequence native and ACP admissions through one batch claim,
  mint queue IDs before admission, preserve each exact operation ID through
  RPC retries within its lease, and reconcile snapshots when a retry returns
  stale. Backend-specific multi-message steering semantics remain unresolved.
- Integration must define how provider evidence resolves an in-doubt claim.
- Integration must decide how queued attachments remain readable while waiting.
- This primitive exposes synchronous ID factories; the daemon RPC contract must
  make those IDs available to the WebUI before enqueue and mutation calls.
- A 15-minute retry lease is a state-primitive default, not a user-facing
  delivery guarantee. Integration must reconcile stale retries by snapshot and
  must not mint a new ID for an operation whose prior result is unknown.
