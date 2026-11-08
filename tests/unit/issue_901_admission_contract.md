# Issue 901 local admission conformance seam

Status: proposed, local-only, not a replay or release contract. No in-memory
state here proves process-wide coverage, durability, or crash recovery. Existing
compaction and existing-destination replacement remain disabled. The companion
tests describe behavior that the current implementation does not yet provide.

## Types and signatures

Module: `kollabor_ai.session_admission` (proposed; does not exist yet).

```python
class AdmissionError(Exception): ...

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
    reason: Literal["pending", "indeterminate", "coverage_unverified"]

class LocalAdmissionFence:
    def __init__(self, key: SessionKey) -> None: ...
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
using a short critical section, or reject a contended `try_snapshot` promptly.
No await, disk I/O, callback, or provider call is permitted inside that section.
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
- Tokens are bound to their SessionKey; a new incarnation with the same session
  name rejects old tokens. The old owner must remain unresolved/fenced until its
  operations are safely reconciled; creating a new fence does not resolve them.
- `try_snapshot` never succeeds in this scaffold, even when no tokens exist or
  all tokens are terminal: global producer coverage and durable state are
  unknown. It returns `pending` for unresolved PENDING or WRITTEN, otherwise
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
