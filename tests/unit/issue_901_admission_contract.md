# Issue 901 local admission conformance seam

Status: proposed, local-only, not a replay or release contract. No in-memory
state here proves process-wide coverage, durability, or crash recovery. Existing
compaction and existing-destination replacement remain disabled. The companion
tests exercise only the isolated local implementation.

## Types and signatures

Module: `kollabor_ai.session_admission` (isolated implementation; not integrated).

```python
class AdmissionError(Exception): ...
class AdmissionBusy(AdmissionError): ...  # local transition retryable, no change

def normalize_synced_user_log_id(value: object, *, session_id: str,
                                 logger_generation: int) -> str: ...

@dataclass(frozen=True)
class SessionKey:
    session_id: str
    incarnation: str  # unique within the owner lifetime, even for the same ID

@dataclass(frozen=True)
class AdmissionToken:
    key: SessionKey
    op_id: str  # unique within the fence, not a durable identifier
    kind: Literal["native", "acp"]

class AdmissionState(Enum):
    PENDING = "pending"      # admitted; no confirmed record
    WRITTEN = "written"      # record confirmed, membership unresolved
    COMMITTED = "committed"  # record correlated to exactly one history row
    ABORTED = "aborted"      # proved no durable record and no history mutation
    INDETERMINATE = "indeterminate"  # write or history outcome cannot be proved

@dataclass(frozen=True)
class SnapshotDecision:
    allowed: bool
    reason: Literal["busy", "pending", "indeterminate", "coverage_unverified"]

class LocalAdmissionFence:
    def __init__(self, key: SessionKey, *, max_entries: int = 4096) -> None: ...
    def reserve(self, kind: Literal["native", "acp"]) -> AdmissionToken: ...
    def mark_written(self, token: AdmissionToken, *, record_id: str) -> None: ...
    def commit_native_batch(self, tokens: tuple[AdmissionToken, ...], *,
                            history_id: str, record_ids: tuple[str, ...]) -> None: ...
    def commit_acp(self, token: AdmissionToken, *, history_id: str,
                   record_id: str) -> None: ...
    def abort_before_write(self, token: AdmissionToken) -> None: ...
    def mark_indeterminate(self, token: AdmissionToken) -> None: ...
    def state(self, token: AdmissionToken) -> AdmissionState: ...
    def try_snapshot(self) -> SnapshotDecision: ...
```

Each method is synchronous and must return without waiting for an async task;
no lock is held across an await. An implementation must serialize access by
using bounded short critical sections. Every method attempts its lock without
waiting: `try_snapshot` returns `busy`; all other methods raise the typed `AdmissionBusy`
with no state change. Capacity, duplicate IDs and invalid transitions raise
`AdmissionError` and must never be treated as retryable contention. Work within a lock is bounded by the maximum capacity.
No await, disk I/O, callback, or provider call is permitted inside that section.
For deterministic contention testing, the implementation exposes its private
`_state_lock` as a standard `threading.Lock`; `try_snapshot` must acquire it
with `blocking=False` and return `busy` if unavailable.
A future cross-process contract needs a distinct durable fence and cannot infer
coverage from this object.

## State and correlation rules

- `reserve` creates a unique PENDING token before enqueue/log/memory mutation.
  Native token stays open through batching; ACP token starts before `_record_user`
  adds either history row or schedules logging.
- `mark_written` requires a confirmed append and transitions PENDING -> WRITTEN.
  A missing receipt, uncertain append, cancellation after a possible write, or
  unconfirmed rollback transitions to INDETERMINATE instead. An indeterminate
  outcome is never automatically cleared or treated as a successful retry.
- `abort_before_write` transitions PENDING -> ABORTED only when the caller can
  prove neither durable record nor history mutation exists. Enqueue refusal or
  cancellation *after* a write or memory mutation requires an ordered durable
  resolution in the full design; this scaffold records INDETERMINATE instead.
  It cannot silently discard a logged input.
- A native batch transitions all its WRITTEN native tokens -> COMMITTED in one
  atomic operation only after the history row exists and the exact ordered,
  duplicate-free `record_ids` match the tokens. Partial, reordered, repeated,
  foreign-session, wrong-kind or missing membership raises AdmissionError and
  changes no state. ACP commit similarly matches its single confirmed record
  and history row. A token cannot be reused or committed twice.
- Record IDs are unique across all tokens in one fence, including terminal
  tokens; history IDs are unique across committed batches and ACP commits.
  Duplicate IDs or invalid transitions raise AdmissionError without any state
  change. COMMITTED, ABORTED and INDETERMINATE are terminal and cannot be
  rewritten or re-used.
- Keep terminal IDs until the fence owner can be retired safely. Bound memory by
  `max_entries` (an integer from 1 through 4096) across pending and terminal tokens; once full, `reserve` raises
  AdmissionError without evicting anything. This is an explicit local-only
  availability limit, not a safe durable compaction or cross-process scheme.
- Tokens are bound to their SessionKey; a new incarnation with the same session
  name rejects old tokens. The old owner must remain unresolved/fenced until its
  operations are safely reconciled; creating a new fence does not resolve them.
- `try_snapshot` never succeeds in this scaffold, even when no tokens exist or
  all tokens are terminal: global producer coverage and durable state are
  unknown. If the state lock is held, it returns `busy` immediately without
  inspecting state. Otherwise it returns `pending` for unresolved PENDING or WRITTEN, otherwise
  `indeterminate` if any outcome is indeterminate, else `coverage_unverified`.
  A future snapshot permit must add owner/generation fencing and an immutable
  capture, under the shared cross-process contract.
- No proposed tombstone authorizes retaining content required to be deleted.
  Voice deletion must meet the actual deletion contract (including relevant
  rewritten logs, sidecars, recovery artifacts and retention policy), not merely
  hide content on replay. That remains a full-fix gate.

The test scaffold exercises only this state machine. It does not claim real
native or ACP wiring, provider/tool behavior, cross-process exclusion, durable
sequence, crash recovery, sidecar correlation, or repaired replay.

## Ownership, errors, and uncovered connectivity

- A token is accepted only by the exact fence instance that issued that exact
  object. Equal SessionKey values on separate fences do not confer ownership;
  even an equal, reconstructed token is rejected. There is no serialization or
  cross-process token contract here. Invalid types, empty/whitespace IDs, empty
  batches, unknown tokens, invalid states, wrong kind, duplicate record/history
  IDs, and exhausted capacity raise AdmissionError without changing any state.
- QueueProcessor currently ignores `_add_message_fn`'s return value, while
  LLMService `_add_conversation_message` returns the manager's UUID after
  manager/history mutation and event publication. Future integration must
  capture that returned ID and commit only after verifying the corresponding
  history membership. An exception can occur after partial or complete mutation
  and before return, including publication failure; mark indeterminate, do not
  assume no row was added. Native log-before-enqueue rejection likewise leaves
  a possibly durable row; it cannot become ABORTED without durable resolution.
- ACP `_record_user`, including steering, creates history before an async append;
  observation/retry/handover and delayed callbacks have distinct lifetimes.
  None is wired to this state machine. MessageHandler may independently add
  history and log, including history-only/log-only modes; native/ACP assistant
  results, tool outputs and other logger/Hub producers are also uncovered.
  `save_conversation` and ACP display/stats/UID sidecars are separate unfenced
  writes. The object grants no snapshot permit for any configuration.
- The existing create-only `save_session` allows an absent destination but
  does not prove its memory snapshot covers all producers; this scaffold does
  not certify creation safety. Existing-destination replacement and compaction
  remain disabled. Do not call this a complete admission or save fix.
- Voice deletion requires removal according to the actual deletion contract
  from relevant log rewrites and all applicable sidecars, recovery artifacts,
  retained snapshots and backups; an append-only tombstone or hidden replay is
  insufficient. Exact recovery/retention design remains unresolved.

- `reserve` must complete successfully before its caller starts any producer
  side effect. Capacity, contention, collision and invalid-kind errors return
  without reserving; callers must not enqueue, log, mutate history or dispatch
  a provider/tool for that operation after refusal. The isolated module cannot
  enforce this caller obligation; producer wiring remains an acceptance gate.
- An equal-value copy of a token is rejected even by its issuing fence. The
  original token object must be retained. Every token-taking method rejects a
  foreign fence token, fabricated token, malformed token, and wrong-kind token
  where a kind-specific commit is required, without changing state.

## Contention after side effects

`reserve` precedes producer side effects, but later `mark_written`, batch/ACP
commit, or `mark_indeterminate` can encounter a busy lock after the caller has
already appended a record or mutated history. Busy changes no fence state and
is neither an abort nor evidence of success. The caller retains the original
token and exact observed record/history IDs and retries only the local state
transition after contention clears; it must not repeat the external append,
history mutation, provider call, or tool action. Until transition succeeds,
PENDING or WRITTEN blocks any future snapshot even if producer wiring exists.

If the caller cannot establish the outcome or cannot complete that local retry,
it must retain/reconcile the unresolved operation; when possible it records
INDETERMINATE, retrying that local transition on contention as well. The local
fence cannot persist this obligation across a crash, cancellation, owner loss,
or process restart. Shared durable admission/recovery and caller integration
are prerequisites for lifting any compaction or replacement gate. This text
specifies a caller obligation; no existing producer implements it here.

## Receipt conversion and order limitations

`normalize_synced_user_log_id` accepts only the actual `SyncedUserLogId` type,
requires confirmed file/immediate-directory sync evidence, matching nonempty
session ID and matching nonnegative local logger generation, and returns a
plain `str` suitable for `mark_written`. Missing, stale, unconfirmed, malformed,
or differently typed values raise AdmissionError; a direct string subclass is
not silently accepted by identifier methods. The caller supplies and must still
validate the current owner/session/generation; the conversion alone proves no
cross-process generation, batch membership, cursor or universal crash safety.

Native batch commit checks only that the caller-supplied IDs match the order of
the caller-supplied tokens. It does not independently know actual append order,
queue order, or batch membership. Reservation order must not be assumed to equal
any of those orders. Capturing and validating the actual queue/log/batch order
is an unresolved integration and durable-recovery gate.
