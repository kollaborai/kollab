# WebUI queued attachment staging

Issue: #1366
Status: Queue/attachment state primitives implemented; daemon integration incomplete
Owner: daemon state / WebUI integration

## Problem

The WebUI sends image parts as base64 data URLs. Engine media storage is local
to the Engine process, so a queued item cannot rely on that file being readable
by a daemon-owned dispatcher. The queue payload cap is 1 MiB while current
image handling accepts 10 MiB per image and up to 20 MiB per message. Embedding
those data URLs in each queue payload either rejects valid images or makes the
queue's payload bound misleading.

## Goal and non-goals

Provide a daemon-process-lifetime store for validated image bytes. Queue items
refer to small opaque IDs, and only the daemon worker can resolve an attachment
for its owning item. Bounds reject an upload before storing it; no pending or
ambiguous item is evicted to make room.

This slice implements an in-memory scoped store and atomic queue admission and
queued-item removal primitives. It does not register RPC methods, add Engine
routes or WebUI APIs, connect a worker to provider input, or copy accepted
images into durable history. It therefore does not yet make queued images
usable end to end.

## What people see

No user-visible change in this slice. The later integrated queue can retain the
existing image composer behavior while storing opaque daemon references in
queue messages. It must return an explicit capacity or validation error before
clearing the composer when staging fails.

## Contract

`kollabor/state/web_message_attachments.py` owns a process-local
`WebMessageAttachmentRegistry`. The trusted caller injects two callbacks:

- `resolve_scope(params, caller_context)` verifies the authenticated local
  operator and the daemon's canonical conversation identity, then returns a
  `AttachmentScopeBinding(scope_id, queue_incarnation)`. RPC parameters never
  choose either value.
- `validate_owner_id(binding, owner_id)` verifies a queue-minted item ID for
  the exact scope and incarnation. It validates issuance, including IDs pinned
  to old or terminal queue items for worker cleanup; it must not trust a
  caller-supplied string or require the item to remain queued. The validator is
  synchronous and must not acquire or await the queue lock; the atomic queue
  transaction calls it while that lock is held.

The registry returns an internal bound-scope handle. Its methods accept no
scope argument; the handle is never serialized. A worker may keep a captured
handle to finish or clean up that old scope after a conversation switch. The
dispatcher must independently revalidate the conversation at provider
admission before sending.

The store accepts raw bytes for `image/png`, `image/jpeg`, `image/jpg`,
`image/webp`, and `image/gif`; `image/jpg` normalizes to `image/jpeg`. Per-image
bytes are capped at 10 MiB and one item is capped at the current 20 MiB total
image limit. Defaults cap one scope at 32 MiB / 64 images, the daemon at 128
MiB / 512 images / 16 active scopes, and each item at 20 image references.
Constructor limits must be positive integers; time limits must be finite,
positive, at least one nanosecond, and the upload retry lease cannot exceed
30 days. Capacity is checked atomically before any attachment or receipt is
admitted. The store does not evict retained data.

Each scope can mint a stable upload operation ID with a 15-minute retry lease.
The exact same operation and bytes return the same reference; changing bytes or
MIME under that ID is an idempotency conflict. Operation IDs are MACed to the
store instance, scope, and queue incarnation. Expired IDs are stale, never new
work. Uploads not linked to a queue item become unusable after at most the
same 15-minute window. Their bytes are reclaimed on the next store operation
or an explicit `prune_expired` call; this primitive starts no background task.

`retain(owner_id, refs)` validates all queue IDs and refs first, then atomically
binds every ref to exactly one owner. A retained ref has no time-based expiry;
long permission waits and in-doubt outcomes cannot delete its image. Replaying
the same owner/ref list is a no-op; trying to add, reorder, or reassign refs
conflicts. `open_for_owner` returns bytes only to that exact bound owner.
`release_owner` removes its refs and returns their capacity; calling it twice is
safe. Accepted items may release only after durable history handoff; queued
removal may release after user confirmation. `not_sent`, timeout, and
`in_doubt` keep ownership. The adapter must atomically link refs with enqueue
or reconcile an uncertain enqueue before releasing anything.

`WebMessageQueue.enqueue_batch_with_attachments(...)` provides that state
transaction for trusted daemon code. It requires a bound attachment capability
whose conversation scope and queue incarnation exactly match the queue. Each
`WebMessage` carries an ordered `attachment_refs` tuple beside its opaque
payload. The queue checks batch IDs, item IDs, queue capacity, receipt capacity,
and retry fingerprints before taking the attachment lock. It then takes locks
in the fixed order queue → attachment store, validates every owner and
reference, checks each item's byte bound, and commits queue rows and all owner
pins synchronously with no intervening await. A missing, expired, foreign,
duplicated, or over-limit reference leaves the queue batch and all new owner
pins absent. Queue capacity rejection also leaves staged references unowned so
the caller can retry or allow their orphan TTL to expire. Ordinary
`enqueue_batch(...)` rejects items with attachment refs, preventing callers
from bypassing the atomic retention path.

The ordered refs stay attached to their queue items in `QueueSnapshot` and
`ClaimPayload`; a dispatcher can obtain them without parsing or duplicating the
opaque item payload. Refs are hidden from object representations and never
include image bytes. The existing RPC snapshot and claim wire serializers do
not yet carry this metadata, and the RPC enqueue/remove handlers still use the
plain queue methods. Authenticated attachment RPC and worker wire integration
remain unwired.

The enqueue retry fingerprint includes the ordered message IDs, payload hashes,
and each item's ordered attachment refs. Repeating the exact operation returns
its receipt without re-pinning; reusing the operation ID with changed refs
conflicts. Cancellation while waiting for either lock unwinds without queue or
owner mutation. After both locks are acquired, the commit contains no await, so
task cancellation cannot split the two in-memory updates. A response timeout after
commit is still ambiguous to the caller: retry the same operation, batch, item
IDs, payloads, and refs; do not release the staged owner until the receipt or a
snapshot confirms the outcome.

`WebMessageQueue.remove_item_with_attachments(...)` removes one queued item and
releases only that item's refs under the same lock order and retry receipt. A
pre-dispatch claim release, `not_sent`, or `in_doubt` settlement leaves refs
pinned. An `accepted` settlement also leaves refs pinned: a trusted worker must
first complete an idempotent durable media-history handoff, then call the
internal owner release. That accepted-image history handoff and its retry state
are not implemented in this slice. Plain `remove_item(...)` rejects an item
with attachment refs so a caller cannot drop the queue item while leaking its
retained storage.

These are internal state APIs only. No authenticated RPC exposes the capability
or calls either composite operation yet. The eventual RPC must bind scope from
trusted caller context, and its upload path must bound concurrent request-body
and base64-decoding memory before fully materializing image bytes. The store's
retained-byte limits apply after the caller supplies decoded bytes and do not
provide that transport-level transient-memory bound.

All blobs and receipts disappear on daemon restart, matching #1366's current
in-memory queue lifetime. Refs from another scope, another queue incarnation,
or another daemon instance are indistinguishable from missing data.

## Scenarios

- If a local authenticated caller binds to conversation A and uploads bytes,
  then a caller bound to B cannot open, retain, or settle A's reference.
- If the client retries the same stage operation with the same bytes and MIME,
  then it receives the same reference without increasing capacity use.
- If it reuses an operation ID with different bytes or MIME, then the store
  rejects it and leaves the original bytes untouched.
- If an item is retained and waits through a permission prompt longer than the
  upload retry lease, then `prune_expired` leaves its bytes readable to that
  item owner.
- If a definitely accepted or removed item releases its owner, then all of its
  refs become unreadable and their capacity can be reused.
- If a staged upload is never retained, then it becomes unusable at expiry and
  is reclaimed on the next store operation or explicit prune; replaying its
  expired operation ID does not recreate the data.
- If per-image, per-scope, daemon-wide, scope-count, or attachment-count
  capacity is exceeded, then the operation is rejected without partial
  admission or eviction.
- If a queue ID is invalid or from another queue incarnation, then retain/open
  fail closed; if the conversation switches after a worker captured its old
  binding, that handle remains scoped to the old queue.
- If enqueue commits a batch with refs and the RPC response is lost, retrying
  the exact IDs, messages, and refs returns the same queue receipt and leaves
  the refs pinned to their original items.
- If one ref in a multi-item batch is invalid, expired, duplicated across
  owners, or over an item's byte limit, then no item is enqueued and no valid
  prefix ref is retained.
- If a queued item is removed through the attachment-aware method, only its
  refs are released; other items in the batch keep their refs.
- If provider acceptance precedes a failed durable history copy, then the
  worker must retain the refs and reconcile the copy without resending the
  provider request. That worker behavior remains unimplemented.

## Failure modes

- Invalid MIME, empty bytes, or an oversized image: reject before mutation.
- Total/scope/item/count capacity exhausted: reject without evicting another
  item or consuming the operation ID.
- Resolver missing, remote, stale, or malformed: deny binding without creating
  a scope.
- Invalid operation MAC, wrong scope, future timestamp, or expired lease:
  reject as invalid/stale; do not reinterpret as a new operation.
- Same operation ID with different input: return an idempotency conflict.
- Retain contains one invalid or foreign reference: reject the entire list.
- Same reference assigned to another owner: conflict and preserve the first
  owner's bytes.
- Enqueue capacity failure: leave every referenced upload as an unowned orphan;
  it remains retryable only until its existing TTL.
- Cancellation before both transaction locks are held: leave the queue and
  owner maps unchanged; releasing the acquired queue lock is handled by the
  async context.
- Unlinked attachment expires: make it unreadable; reclaim only that orphan
  and its bytes on the next store operation or explicit prune, retaining a
  bounded retry tombstone until its lease ends.
- Cleanup is repeated after settlement: return success/no-op without affecting
  another item's references.
- Daemon restart: queue and stage data are both in memory and are lost together;
  this slice does not provide cross-restart durability.

## Decisions

- Use daemon memory, not Engine-local files: the queue is daemon-process local,
  and process restart drops both queue and stage data without filesystem
  janitor risk.
- Keep scope derivation and queue-ID validation injected: the primitive does
  not invent an authorization identity or accept a request-selected scope.
- Use orphan TTL plus explicit item ownership: time-based pruning bounds failed
  uploads, while referenced data survives long permissions and ambiguity.
- Keep staging, queue mutation, and provider admission in separate modules;
  the queue and store meet only through the internal atomic composite methods.
  The eventual dispatcher still must revalidate the current conversation at
  provider admission.

## Test plan

Red-first unit tests:

- `test_stage_replay_is_idempotent_and_conflicting_bytes_are_rejected`
- `test_rejects_bad_media_and_single_image_over_limit_without_mutation`
- `test_accepts_ten_mib_image_and_rejects_one_byte_over_default_limit`
- `test_scope_and_global_caps_reject_before_partial_admission`
- `test_scope_count_capacity_releases_only_after_orphan_expiry`
- `test_concurrent_same_operation_writes_one_attachment`
- `test_concurrent_capacity_race_admits_no_overflow`
- `test_retained_attachment_survives_long_wait_and_retry_lease_expiry`
- `test_unreferenced_upload_expires_and_old_id_cannot_recreate_it`
- `test_release_owner_removes_only_its_attachments_and_is_idempotent`
- `test_item_byte_cap_rejects_all_refs_without_partial_retention`
- `test_refs_and_owner_ids_cannot_cross_scope_or_be_spoofed`
- `test_retain_validates_every_ref_before_pinning_any`
- `test_scope_binding_fails_closed_for_remote_or_stale_conversation`
- `test_enqueue_with_attachments_is_atomic_ordered_and_idempotent`
- `test_bad_reference_rejects_entire_batch_without_retaining_valid_prefix`
- `test_plain_enqueue_rejects_attachment_metadata_without_atomic_store`
- `test_repeated_reference_cannot_be_retained_by_two_batch_items`
- `test_per_item_byte_limit_rejects_all_refs_without_partial_retention`
- `test_queue_capacity_rejection_keeps_upload_unowned_for_retry_or_expiry`
- `test_expired_orphan_is_rejected_but_retained_reference_survives_pruning`
- `test_cancellation_waiting_for_attachment_lock_leaves_both_ledgers_unchanged`
- `test_attachment_scope_from_another_queue_incarnation_is_denied`
- `test_attachment_claims_keep_refs_private_from_change_notifications`
- `test_terminal_outcomes_keep_refs_until_handoff_and_remove_releases_exact_item`

Run the focused unit file. Live RPC, Engine, provider, WebUI, and daemon-restart
proof belong to the later integration work; no live integration is claimed by
this primitive.

## Docs and changelog fragment

This spec is the contract for the standalone primitive. No user-facing
changelog fragment is added until the Engine and WebUI can stage, display, and
dispatch queued images.

## Open questions

- Integrate `retain` and queue enqueue under one daemon transaction/lock, or
  reconcile upload ownership against queue snapshots before any orphan pruning.
- Define the Engine-to-daemon single-image upload route, preview retrieval,
  and provider adapter that consumes an owned reference.
- Add attachment ownership cleanup on accepted, removed, and definite rejected
  queue outcomes; retain on `not_sent` and `in_doubt`.
- Copy accepted images into the durable history media store before releasing
  daemon staging refs, or define a daemon-owned history media path.
