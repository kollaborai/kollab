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

Each operation has a stable `operation_id`; each envelope has a stable
`batch_id`; each item has a stable `request_id`. Replaying the same operation
and payload returns its receipt without a second mutation. Reusing an operation
ID with different arguments is a conflict. A claim replay keeps the same claim
ID but reports its current status and revision; it never returns payloads.
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

Default bounds are 100 pending items, 20 items per batch, 1 MiB per opaque
payload, 8 MiB total pending payload, and 4096 distinct operation receipts per
daemon lifetime. Exceeding a bound rejects the whole operation before state
changes. The operation receipt ledger does not evict entries; once full, new
mutations are rejected to preserve idempotency. Integrating code must surface
these rejections and must not log payloads.

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

## Failure modes

- Duplicate operation ID with changed arguments: conflict; no mutation.
- Duplicate batch or request ID under a different operation: conflict; no
  mutation.
- Queue, payload, batch, or replay-ledger capacity exceeded: explicit bounded
  rejection; no partial enqueue.
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
- The replay ledger fails closed at its configured bound rather than evicting
  keys and risking duplicate admission. Production integration must decide
  whether to persist this ledger or define a safe retention policy.

## Test plan

Red-first unit tests cover batch order, all-or-none capacity rejection,
idempotent replay and payload conflict, private payload reprs, revision and
incarnation behavior, atomic Send Now promotion/claim, per-item accepted
prefixes, in-doubt middle items, recoverable suffixes under both explicit
resolution outcomes, removal/claim races, and operation ledger bounds. Live
proof is not part of this primitive-only change.

## Docs and changelog fragment

This file is the canonical contract for the state primitive. A changelog
fragment belongs with the later integration PR that makes the feature visible
to users.

## Open questions

- Integration must sequence native and ACP admissions through one batch claim,
  settle each item before claiming the next, and preserve operation IDs through
  RPC retries. Backend-specific multi-message steering semantics remain
  unresolved.
- Integration must define how provider evidence resolves an in-doubt claim.
- Integration must decide how queued attachments remain readable while waiting.
- This primitive bounds receipts by refusing new mutations when full. A
  durable ledger or bounded retry window is needed if that limit is unsuitable
  for a long-lived daemon.
