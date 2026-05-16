# Hub DNS Trust And Delivery Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make hub delivery reliable for local and remote agents by separating identity, liveness, trust, transport, mailbox, and wake decisions.

**Architecture:** DNS becomes durable identity and trust metadata, not the hot-path delivery arbiter. Presence owns live local sockets, mailbox owns durable delivery, remote envelopes own cross-server authenticity, and wake classification owns whether the LLM should run.

**Tech Stack:** Python 3.12, asyncio, file-backed hub mailboxes, existing hub DNS registry, existing `HubMessage`, pytest, ruff.

---

## Current Problem

The app has four different concepts collapsed into one loose "DNS" path:

1. identity: the stable name, such as `lapis`, `sapphire`, or `koordinator`
2. liveness: whether a process is online right now
3. trust: whether that identity is approved or rejected
4. delivery: where a message should go and whether it is accepted

That collapse caused the observed failure:

1. an agent was approved
2. its local DNS record went stale
3. liveness cleanup deleted or hid the record
4. `_route_message()` treated the sender as `unknown`
5. the sender's final report was blocked before it reached koordinator
6. koordinator never woke and never gave the promised consolidated report

The fix is not a rewrite. The fix is a hard boundary:

```text
identity/trust    durable DNS registry
liveness          presence snapshots and endpoint freshness
delivery          delivery policy + socket/mailbox/remote transport
wake              wake classifier and pending reply tracker
```

## Current Emergency Patch

These files already contain an emergency reliability patch in the current dirty tree:

- `packages/kollabor-ai/src/kollabor_ai/providers/errors.py`
- `packages/kollabor-ai/src/kollabor_ai/api_communication_service.py`
- `plugins/hub/dns/registry.py`
- `plugins/hub/messenger.py`
- `plugins/hub/plugin.py`
- `tests/unit/test_ghost_response.py`
- `tests/unit/test_hub_identity_mailbox.py`
- `tests/unit/test_hub_mesh_force.py`

The patch currently does this:

- adds `EmptyResponseError` for ghost empty provider responses
- retries empty provider responses instead of treating them as valid assistant turns
- keeps the current agent's own DNS designation alive during liveness refresh
- queues offline direct messages by stable identity such as `koordinator`
- reads both ephemeral `agent_id` and stable identity mailboxes
- makes `hub_msg` fail explicitly when a message is rejected
- lets forced hub messages bypass sender approval correctly
- tags coordinator task-like hub messages as `task_assignment`

The patch has already passed:

```bash
python -m pytest tests/unit/test_hub_mesh_force.py tests/unit/test_hub_identity_mailbox.py tests/unit/test_ghost_response.py -q
python -m ruff check plugins/hub/plugin.py plugins/hub/messenger.py plugins/hub/dns/registry.py tests/unit/test_hub_mesh_force.py tests/unit/test_hub_identity_mailbox.py tests/unit/test_ghost_response.py
python -m pytest tests/unit/test_ghost_response.py tests/unit/test_hub_mesh_force.py tests/unit/test_hub_identity_mailbox.py tests/unit/test_hub_msg_parsing.py tests/unit/test_hub_wake_order.py tests/unit/llm/test_agent_hud.py tests/unit/llm/test_queue_processor.py -q
./scripts/stabilization-gate.sh
```

Expected results already observed:

```text
8 passed
All checks passed!
85 passed
132 passed
```

## File Structure

Create:

- `plugins/hub/delivery.py`
  - owns delivery classification, policy decisions, trace events, and route outcomes
- `plugins/hub/remote_envelope.py`
  - owns signed remote hub envelope creation and verification
- `tests/unit/test_hub_dns_liveness.py`
  - proves trust records survive liveness churn
- `tests/unit/test_hub_delivery_policy.py`
  - proves local and remote route decisions are explicit
- `tests/unit/test_hub_delivery_trace.py`
  - proves dropped, queued, socket-sent, injected, and rejected messages are traceable
- `tests/unit/test_hub_remote_trust.py`
  - proves remote signed envelope verification and unknown remote quarantine behavior
- `tests/unit/test_hub_pending_replies.py`
  - proves task assignments create expected replies and final reports resolve them

Modify:

- `plugins/hub/dns/models.py`
  - split endpoint freshness from trust state without changing existing serialized records destructively
- `plugins/hub/dns/registry.py`
  - stop deleting trust records during liveness refresh
  - mark endpoints stale instead
  - preserve self, approved local identities, and rejected identities
- `plugins/hub/dns/storage.py`
  - add locked merge-save so concurrent agents do not clobber approval records
- `plugins/hub/models.py`
  - add delivery metadata fields if existing `metadata` keys become too loose
- `plugins/hub/messenger.py`
  - durable identity mailbox reads and writes
  - outbox trace writes for remote and offline delivery
- `plugins/hub/plugin.py`
  - move route decision logic to `plugins/hub/delivery.py`
  - use durable-first direct delivery
  - stop using DNS unknown as a silent local hard-block
  - keep wake classification separate from delivery classification
- `plugins/hub/task_ledger.py`
  - track expected replies from task assignments
  - resolve expected replies when final report evidence arrives
- `plugins/hub/session_state.py`
  - persist pending reply promises across restart when available
- `docs/architecture/reference/agent-dns-reference.md`
  - document identity, liveness, trust, delivery, remote envelope, and mailbox rules
- `scripts/stabilization-gate.sh`
  - include the new focused hub hardening tests

## Delivery Policy Rules

Use these rules everywhere hub messages are routed:

```text
local approved sender          deliver
local self sender              deliver
local rejected sender          reject with explicit error
local unknown same project     deliver with warning and trace unless strict mode is enabled
remote approved sender         deliver after envelope verification
remote unknown sender          quarantine and display/log, no LLM wake
remote rejected sender         reject with explicit error
offline direct target          durable identity mailbox write
socket failure direct target   durable identity mailbox write
broadcast socket failure       trace failure, no fake success
```

`standing by` is never a delivery decision. It only matters to wake classification.

## Delivery Trace States

Every routed message should be inspectable through a compact trace:

```text
created
route_started
sender_policy_checked
queued_identity_mailbox
socket_send_attempted
socket_send_failed
socket_send_succeeded
remote_envelope_verified
remote_envelope_rejected
quarantined
injected_to_llm
wake_decision
route_finished
```

The trace can live in JSONL under the existing hub project state directory.
It must be small, append-only, and keyed by `message.id`.

---

### Task 0: Preserve And Commit The Emergency Patch

**Files:**

- Modify: `packages/kollabor-ai/src/kollabor_ai/providers/errors.py`
- Modify: `packages/kollabor-ai/src/kollabor_ai/api_communication_service.py`
- Modify: `plugins/hub/dns/registry.py`
- Modify: `plugins/hub/messenger.py`
- Modify: `plugins/hub/plugin.py`
- Test: `tests/unit/test_ghost_response.py`
- Test: `tests/unit/test_hub_identity_mailbox.py`
- Test: `tests/unit/test_hub_mesh_force.py`

- [ ] **Step 1: Review the current dirty patch**

Run:

```bash
git status --short
git diff -- packages/kollabor-ai/src/kollabor_ai/providers/errors.py packages/kollabor-ai/src/kollabor_ai/api_communication_service.py plugins/hub/dns/registry.py plugins/hub/messenger.py plugins/hub/plugin.py tests/unit/test_ghost_response.py tests/unit/test_hub_identity_mailbox.py tests/unit/test_hub_mesh_force.py
```

Expected:

```text
only the emergency ghost-response, identity mailbox, force routing, and DNS self-preservation changes appear
```

- [ ] **Step 2: Run the focused emergency tests**

Run:

```bash
python -m pytest tests/unit/test_hub_mesh_force.py tests/unit/test_hub_identity_mailbox.py tests/unit/test_ghost_response.py -q
```

Expected:

```text
8 passed
```

- [ ] **Step 3: Run the hub and HUD regression slice**

Run:

```bash
python -m pytest tests/unit/test_ghost_response.py tests/unit/test_hub_mesh_force.py tests/unit/test_hub_identity_mailbox.py tests/unit/test_hub_msg_parsing.py tests/unit/test_hub_wake_order.py tests/unit/llm/test_agent_hud.py tests/unit/llm/test_queue_processor.py -q
```

Expected:

```text
85 passed
```

- [ ] **Step 4: Run the stabilization gate**

Run:

```bash
./scripts/stabilization-gate.sh
```

Expected:

```text
132 passed
```

- [ ] **Step 5: Commit the emergency patch**

Run:

```bash
git add packages/kollabor-ai/src/kollabor_ai/providers/errors.py packages/kollabor-ai/src/kollabor_ai/api_communication_service.py plugins/hub/dns/registry.py plugins/hub/messenger.py plugins/hub/plugin.py tests/unit/test_ghost_response.py tests/unit/test_hub_identity_mailbox.py tests/unit/test_hub_mesh_force.py
git commit -m "fix: preserve hub delivery through stale dns state"
```

Expected:

```text
commit created with no attribution footer
```

---

### Task 1: Add Explicit Delivery Policy

**Files:**

- Create: `plugins/hub/delivery.py`
- Modify: `plugins/hub/plugin.py`
- Test: `tests/unit/test_hub_delivery_policy.py`

- [ ] **Step 1: Write delivery policy tests**

Create `tests/unit/test_hub_delivery_policy.py`:

```python
from plugins.hub.delivery import DeliveryDecision, DeliveryPolicy, SenderContext


def test_local_self_sender_delivers_even_when_dns_unknown():
    policy = DeliveryPolicy(strict_local_unknown=False)
    decision = policy.decide_sender(
        SenderContext(
            sender="lapis",
            is_self=True,
            is_coordinator=False,
            is_remote=False,
            approval_state="unknown",
            same_project=True,
            force=False,
        )
    )

    assert decision == DeliveryDecision(
        mode="deliver",
        reason="local self sender",
        wake_allowed=True,
        trace_level="info",
    )


def test_local_unknown_same_project_warns_but_delivers_by_default():
    policy = DeliveryPolicy(strict_local_unknown=False)
    decision = policy.decide_sender(
        SenderContext(
            sender="sapphire",
            is_self=False,
            is_coordinator=False,
            is_remote=False,
            approval_state="unknown",
            same_project=True,
            force=False,
        )
    )

    assert decision.mode == "deliver"
    assert decision.reason == "local unknown same project"
    assert decision.trace_level == "warning"


def test_local_unknown_same_project_rejects_in_strict_mode():
    policy = DeliveryPolicy(strict_local_unknown=True)
    decision = policy.decide_sender(
        SenderContext(
            sender="sapphire",
            is_self=False,
            is_coordinator=False,
            is_remote=False,
            approval_state="unknown",
            same_project=True,
            force=False,
        )
    )

    assert decision.mode == "reject"
    assert decision.reason == "local unknown sender in strict mode"


def test_remote_unknown_quarantines_without_wake():
    policy = DeliveryPolicy(strict_local_unknown=False)
    decision = policy.decide_sender(
        SenderContext(
            sender="remote-lapis",
            is_self=False,
            is_coordinator=False,
            is_remote=True,
            approval_state="unknown",
            same_project=False,
            force=False,
        )
    )

    assert decision.mode == "quarantine"
    assert decision.reason == "remote unknown sender"
    assert decision.wake_allowed is False


def test_rejected_sender_fails_even_with_same_project():
    policy = DeliveryPolicy(strict_local_unknown=False)
    decision = policy.decide_sender(
        SenderContext(
            sender="aquamarine",
            is_self=False,
            is_coordinator=False,
            is_remote=False,
            approval_state="rejected",
            same_project=True,
            force=False,
        )
    )

    assert decision.mode == "reject"
    assert decision.reason == "sender rejected"
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
python -m pytest tests/unit/test_hub_delivery_policy.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'plugins.hub.delivery'
```

- [ ] **Step 3: Implement the policy module**

Create `plugins/hub/delivery.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SenderContext:
    sender: str
    is_self: bool
    is_coordinator: bool
    is_remote: bool
    approval_state: str
    same_project: bool
    force: bool = False


@dataclass(frozen=True)
class DeliveryDecision:
    mode: str
    reason: str
    wake_allowed: bool
    trace_level: str = "info"


class DeliveryPolicy:
    def __init__(self, *, strict_local_unknown: bool = False):
        self.strict_local_unknown = strict_local_unknown

    def decide_sender(self, context: SenderContext) -> DeliveryDecision:
        if context.is_coordinator:
            return DeliveryDecision("deliver", "coordinator sender", True)
        if context.is_self:
            return DeliveryDecision("deliver", "local self sender", True)
        if context.force:
            return DeliveryDecision("deliver", "force sender override", True, "warning")
        if context.approval_state == "rejected":
            return DeliveryDecision("reject", "sender rejected", False, "warning")
        if context.approval_state in {"approved", "auto_approved"}:
            return DeliveryDecision("deliver", "sender approved", True)
        if context.is_remote:
            return DeliveryDecision("quarantine", "remote unknown sender", False, "warning")
        if context.same_project and not self.strict_local_unknown:
            return DeliveryDecision(
                "deliver",
                "local unknown same project",
                True,
                "warning",
            )
        return DeliveryDecision(
            "reject",
            "local unknown sender in strict mode",
            False,
            "warning",
        )
```

- [ ] **Step 4: Run the test and verify pass**

Run:

```bash
python -m pytest tests/unit/test_hub_delivery_policy.py -q
```

Expected:

```text
5 passed
```

- [ ] **Step 5: Wire `_route_message()` to the policy**

Modify `plugins/hub/plugin.py` inside `_route_message()`:

```python
from .delivery import DeliveryPolicy, SenderContext
```

Replace the sender approval block with a helper call:

```python
decision = self._decide_sender_delivery(message)
if decision.mode == "reject":
    logger.warning(
        "Message from %s rejected: %s",
        self._identity.identity if self._identity else message.from_identity,
        decision.reason,
    )
    return [("mesh", decision.reason)]
if decision.mode == "quarantine":
    await self._quarantine_hub_message(message, decision.reason)
    return [("mesh", decision.reason)]
if decision.trace_level == "warning":
    logger.warning(
        "Message from %s allowed with warning: %s",
        self._identity.identity if self._identity else message.from_identity,
        decision.reason,
    )
```

Add the helper near `_route_message()`:

```python
def _decide_sender_delivery(self, message: HubMessage):
    approval_state = "unknown"
    if self._dns_registry and self._identity:
        record = self._dns_registry.resolve(self._identity.identity)
        approval_state = record.approval_state if record else "unknown"

    same_project = True
    is_remote = bool((message.metadata or {}).get("remote"))
    strict_local_unknown = bool(
        self.config
        and self.config.get("plugins.hub.strict_local_unknown_senders", False)
    )
    policy = DeliveryPolicy(strict_local_unknown=strict_local_unknown)
    return policy.decide_sender(
        SenderContext(
            sender=self._identity.identity if self._identity else message.from_identity,
            is_self=True,
            is_coordinator=bool(self._identity and self._identity.is_coordinator),
            is_remote=is_remote,
            approval_state=approval_state,
            same_project=same_project,
            force=bool(getattr(message, "force", False)),
        )
    )
```

- [ ] **Step 6: Add quarantine helper**

Add to `plugins/hub/plugin.py`:

```python
async def _quarantine_hub_message(self, message: HubMessage, reason: str) -> None:
    if message.metadata is None:
        message.metadata = {}
    message.metadata["quarantined"] = True
    message.metadata["quarantine_reason"] = reason
    target = self._identity.identity if self._identity else "unknown"
    await AgentMessenger.send_to_file(f"{target}.quarantine", message)
```

- [ ] **Step 7: Run policy and existing mesh tests**

Run:

```bash
python -m pytest tests/unit/test_hub_delivery_policy.py tests/unit/test_hub_mesh_force.py tests/unit/test_hub_identity_mailbox.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 8: Commit**

Run:

```bash
git add plugins/hub/delivery.py plugins/hub/plugin.py tests/unit/test_hub_delivery_policy.py
git commit -m "feat: add explicit hub delivery policy"
```

Expected:

```text
commit created with no attribution footer
```

---

### Task 2: Separate DNS Trust From Liveness

**Files:**

- Modify: `plugins/hub/dns/models.py`
- Modify: `plugins/hub/dns/registry.py`
- Modify: `plugins/hub/dns/storage.py`
- Test: `tests/unit/test_hub_dns_liveness.py`

- [ ] **Step 1: Write liveness tests**

Create `tests/unit/test_hub_dns_liveness.py`:

```python
import time

from plugins.hub.dns.models import AgentRecord
from plugins.hub.dns.registry import DNSRegistry


def test_liveness_marks_approved_record_stale_without_deleting(tmp_path):
    registry = DNSRegistry(storage_dir=tmp_path)
    registry.register(
        AgentRecord(
            designation="sapphire",
            agent_id="old-session-id",
            approval_state="approved",
            last_seen=time.time() - 120,
            ttl=1,
            socket_path="/tmp/missing.sock",
        )
    )

    removed = registry.refresh_liveness([])
    record = registry.resolve("sapphire")

    assert removed == 0
    assert record is not None
    assert record.approval_state == "approved"
    assert record.state == "offline"
    assert record.tags["endpoint_state"] == "stale"


def test_liveness_keeps_rejected_record_for_audit(tmp_path):
    registry = DNSRegistry(storage_dir=tmp_path)
    registry.register(
        AgentRecord(
            designation="bad-agent",
            agent_id="bad-session",
            approval_state="rejected",
            last_seen=time.time() - 120,
            ttl=1,
        )
    )

    removed = registry.refresh_liveness([])
    record = registry.resolve("bad-agent")

    assert removed == 0
    assert record is not None
    assert record.approval_state == "rejected"
    assert record.tags["endpoint_state"] == "stale"
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
python -m pytest tests/unit/test_hub_dns_liveness.py -q
```

Expected:

```text
at least one test fails because stale records are deleted or not marked endpoint_state=stale
```

- [ ] **Step 3: Add endpoint freshness fields**

Modify `plugins/hub/dns/models.py` by adding fields to `AgentRecord`:

```python
endpoint_state: str = "fresh"
last_endpoint_seen: float = field(default_factory=time.time)
```

Add to `to_dict()`:

```python
"endpoint_state": self.endpoint_state,
"last_endpoint_seen": self.last_endpoint_seen,
```

Keep `from_dict()` unchanged except for the dataclass fields being accepted.

- [ ] **Step 4: Update `refresh_liveness()`**

Modify `plugins/hub/dns/registry.py`:

```python
def _mark_endpoint_stale(self, record: AgentRecord) -> None:
    record.state = "offline"
    record.endpoint_state = "stale"
    record.tags["endpoint_state"] = "stale"
```

Then in `refresh_liveness()` replace deletion of approved or rejected records:

```python
elif record.is_stale:
    if record.approval_state in {"approved", "auto_approved", "rejected"}:
        self._mark_endpoint_stale(record)
    else:
        stale.append(designation)
```

When a live agent is seen:

```python
record.last_seen = time.time()
record.last_endpoint_seen = record.last_seen
record.endpoint_state = "fresh"
record.tags["endpoint_state"] = "fresh"
```

- [ ] **Step 5: Add merge-save API**

Modify `plugins/hub/dns/storage.py`:

```python
def merge_save_registry(self, records: Dict[str, AgentRecord]) -> None:
    existing = self.load_registry()
    merged = {**existing, **records}
    self.save_registry(merged)
```

Modify `DNSRegistry._save()` to use `merge_save_registry()` when present:

```python
if hasattr(self._storage, "merge_save_registry"):
    self._storage.merge_save_registry(self._records)
else:
    self._storage.save_registry(self._records)
```

- [ ] **Step 6: Run DNS tests**

Run:

```bash
python -m pytest tests/unit/test_hub_dns_liveness.py tests/unit/test_hub_mesh_force.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 7: Commit**

Run:

```bash
git add plugins/hub/dns/models.py plugins/hub/dns/registry.py plugins/hub/dns/storage.py tests/unit/test_hub_dns_liveness.py
git commit -m "feat: separate hub dns trust from liveness"
```

Expected:

```text
commit created with no attribution footer
```

---

### Task 3: Add Durable Delivery Trace

**Files:**

- Modify: `plugins/hub/delivery.py`
- Modify: `plugins/hub/messenger.py`
- Modify: `plugins/hub/plugin.py`
- Test: `tests/unit/test_hub_delivery_trace.py`

- [ ] **Step 1: Write trace tests**

Create `tests/unit/test_hub_delivery_trace.py`:

```python
import json

from plugins.hub.delivery import DeliveryTrace


def test_trace_appends_compact_jsonl(tmp_path):
    trace = DeliveryTrace(tmp_path / "delivery_trace.jsonl")

    trace.record(
        message_id="msg-1",
        event="created",
        sender="lapis",
        target="koordinator",
        detail="manual task assignment",
    )
    trace.record(
        message_id="msg-1",
        event="queued_identity_mailbox",
        sender="lapis",
        target="koordinator",
        detail="offline direct target",
    )

    lines = (tmp_path / "delivery_trace.jsonl").read_text().splitlines()
    payloads = [json.loads(line) for line in lines]

    assert [p["event"] for p in payloads] == [
        "created",
        "queued_identity_mailbox",
    ]
    assert payloads[0]["message_id"] == "msg-1"
    assert payloads[1]["target"] == "koordinator"
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
python -m pytest tests/unit/test_hub_delivery_trace.py -q
```

Expected:

```text
ImportError for DeliveryTrace
```

- [ ] **Step 3: Implement trace writer**

Add to `plugins/hub/delivery.py`:

```python
import json
import time
from pathlib import Path


class DeliveryTrace:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        message_id: str,
        event: str,
        sender: str,
        target: str,
        detail: str,
    ) -> None:
        payload = {
            "ts": time.time(),
            "message_id": message_id,
            "event": event,
            "sender": sender,
            "target": target,
            "detail": detail,
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
```

- [ ] **Step 4: Wire trace points**

Modify `plugins/hub/plugin.py` to initialize trace lazily:

```python
def _delivery_trace(self):
    from .delivery import DeliveryTrace
    from .messenger import get_hub_dir

    if not hasattr(self, "_hub_delivery_trace"):
        self._hub_delivery_trace = DeliveryTrace(
            get_hub_dir() / "delivery_trace.jsonl"
        )
    return self._hub_delivery_trace
```

Record events in `_route_message()`:

```python
self._delivery_trace().record(
    message_id=message.id,
    event="route_started",
    sender=message.from_identity,
    target=message.to,
    detail=message.action,
)
```

Record mailbox fallback:

```python
self._delivery_trace().record(
    message_id=message.id,
    event="queued_identity_mailbox",
    sender=message.from_identity,
    target=message.to,
    detail="offline direct target",
)
```

Record socket send success/failure in `_deliver_to_agent()`:

```python
self._delivery_trace().record(
    message_id=message.id,
    event="socket_send_succeeded" if success else "socket_send_failed",
    sender=message.from_identity,
    target=agent.identity,
    detail=agent.socket_path,
)
```

- [ ] **Step 5: Run trace and hub tests**

Run:

```bash
python -m pytest tests/unit/test_hub_delivery_trace.py tests/unit/test_hub_identity_mailbox.py tests/unit/test_hub_mesh_force.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 6: Commit**

Run:

```bash
git add plugins/hub/delivery.py plugins/hub/plugin.py tests/unit/test_hub_delivery_trace.py
git commit -m "feat: add durable hub delivery trace"
```

Expected:

```text
commit created with no attribution footer
```

---

### Task 4: Add Remote Envelope Verification

**Files:**

- Create: `plugins/hub/remote_envelope.py`
- Modify: `plugins/hub/plugin.py`
- Test: `tests/unit/test_hub_remote_trust.py`

- [ ] **Step 1: Write remote trust tests**

Create `tests/unit/test_hub_remote_trust.py`:

```python
from plugins.hub.remote_envelope import (
    RemoteEnvelope,
    RemoteEnvelopeVerifier,
)


def test_unsigned_remote_message_is_rejected():
    verifier = RemoteEnvelopeVerifier(approved_keys={"remote-lapis": "pubkey"})

    result = verifier.verify(
        RemoteEnvelope(
            sender="remote-lapis",
            authority="kollabor.ai",
            message_id="msg-1",
            timestamp=123.0,
            body_hash="abc",
            signature="",
        )
    )

    assert result.accepted is False
    assert result.reason == "missing signature"


def test_unknown_remote_sender_is_quarantined():
    verifier = RemoteEnvelopeVerifier(approved_keys={})

    result = verifier.verify(
        RemoteEnvelope(
            sender="remote-lapis",
            authority="remote.example",
            message_id="msg-2",
            timestamp=123.0,
            body_hash="abc",
            signature="sig",
        )
    )

    assert result.accepted is False
    assert result.reason == "unknown remote sender"
    assert result.quarantine is True
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
python -m pytest tests/unit/test_hub_remote_trust.py -q
```

Expected:

```text
ModuleNotFoundError for plugins.hub.remote_envelope
```

- [ ] **Step 3: Implement envelope scaffolding**

Create `plugins/hub/remote_envelope.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RemoteEnvelope:
    sender: str
    authority: str
    message_id: str
    timestamp: float
    body_hash: str
    signature: str


@dataclass(frozen=True)
class RemoteVerificationResult:
    accepted: bool
    reason: str
    quarantine: bool = False


class RemoteEnvelopeVerifier:
    def __init__(self, *, approved_keys: dict[str, str]):
        self.approved_keys = approved_keys

    def verify(self, envelope: RemoteEnvelope) -> RemoteVerificationResult:
        if not envelope.signature:
            return RemoteVerificationResult(False, "missing signature")
        if envelope.sender not in self.approved_keys:
            return RemoteVerificationResult(False, "unknown remote sender", True)
        return RemoteVerificationResult(True, "remote sender approved")
```

This task intentionally lands the contract first. Ed25519 signature validation should be added in the next remote-transport slice after the project decides which key library is canonical.

- [ ] **Step 4: Mark remote messages before delivery policy**

Modify `plugins/hub/plugin.py` where incoming remote transport messages are decoded. Set:

```python
message.metadata["remote"] = True
message.metadata["remote_authority"] = envelope.authority
message.metadata["remote_verified"] = result.accepted
message.metadata["remote_verification_reason"] = result.reason
```

If `result.quarantine`:

```python
await self._quarantine_hub_message(message, result.reason)
return
```

- [ ] **Step 5: Run remote tests**

Run:

```bash
python -m pytest tests/unit/test_hub_remote_trust.py tests/unit/test_hub_delivery_policy.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 6: Commit**

Run:

```bash
git add plugins/hub/remote_envelope.py plugins/hub/plugin.py tests/unit/test_hub_remote_trust.py
git commit -m "feat: add remote hub envelope verification contract"
```

Expected:

```text
commit created with no attribution footer
```

---

### Task 5: Track Expected Replies And Ghost Reports

**Files:**

- Modify: `plugins/hub/task_ledger.py`
- Modify: `plugins/hub/session_state.py`
- Modify: `plugins/hub/plugin.py`
- Test: `tests/unit/test_hub_pending_replies.py`

- [ ] **Step 1: Write pending reply tests**

Create `tests/unit/test_hub_pending_replies.py`:

```python
from plugins.hub.task_ledger import HubTaskLedger


def test_task_assignment_creates_expected_reply(tmp_path):
    ledger = HubTaskLedger(tmp_path / "tasks.json")

    ledger.expect_reply(
        task_id="review-agent-hud",
        assignee="lapis",
        requested_by="koordinator",
        message_id="msg-1",
        deadline_seconds=120,
    )

    pending = ledger.pending_replies()

    assert len(pending) == 1
    assert pending[0]["task_id"] == "review-agent-hud"
    assert pending[0]["assignee"] == "lapis"


def test_completion_report_resolves_expected_reply(tmp_path):
    ledger = HubTaskLedger(tmp_path / "tasks.json")
    ledger.expect_reply(
        task_id="review-agent-hud",
        assignee="sapphire",
        requested_by="koordinator",
        message_id="msg-1",
        deadline_seconds=120,
    )

    resolved = ledger.resolve_reply(
        assignee="sapphire",
        evidence="VERDICT: ship-ready, no blockers",
        message_id="msg-2",
    )

    assert resolved is True
    assert ledger.pending_replies() == []
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
python -m pytest tests/unit/test_hub_pending_replies.py -q
```

Expected:

```text
AttributeError for expect_reply or pending_replies
```

- [ ] **Step 3: Add ledger methods**

Modify `plugins/hub/task_ledger.py`:

```python
def expect_reply(
    self,
    *,
    task_id: str,
    assignee: str,
    requested_by: str,
    message_id: str,
    deadline_seconds: int,
) -> None:
    self._state.setdefault("expected_replies", []).append(
        {
            "task_id": task_id,
            "assignee": assignee,
            "requested_by": requested_by,
            "message_id": message_id,
            "created_at": time.time(),
            "deadline_seconds": deadline_seconds,
            "status": "pending",
        }
    )
    self._save()


def pending_replies(self) -> list[dict]:
    return [
        item
        for item in self._state.get("expected_replies", [])
        if item.get("status") == "pending"
    ]


def resolve_reply(self, *, assignee: str, evidence: str, message_id: str) -> bool:
    strong_markers = ("task complete", "shipped", "resolved", "verdict", "no blockers")
    if not any(marker in evidence.lower() for marker in strong_markers):
        return False
    for item in self._state.get("expected_replies", []):
        if item.get("assignee") == assignee and item.get("status") == "pending":
            item["status"] = "resolved"
            item["resolved_by_message_id"] = message_id
            item["resolved_at"] = time.time()
            self._save()
            return True
    return False
```

- [ ] **Step 4: Wire assignments**

Modify `plugins/hub/plugin.py` in `_handle_hub_msg_tool()` after `task_assignment` metadata is set:

```python
if metadata.get("task_assignment") and self._task_ledger:
    self._task_ledger.expect_reply(
        task_id=metadata.get("task_id") or msg.id,
        assignee=target,
        requested_by=self._identity.identity if self._identity else "unknown",
        message_id=msg.id,
        deadline_seconds=metadata.get("deadline_seconds", 120),
    )
```

- [ ] **Step 5: Wire final report resolution**

Modify the incoming hub message path in `plugins/hub/plugin.py` after wake classification:

```python
if self._task_ledger and wake_decision.mode == "wake":
    self._task_ledger.resolve_reply(
        assignee=message.from_identity,
        evidence=message.content,
        message_id=message.id,
    )
```

- [ ] **Step 6: Expose pending replies in Agent HUD**

Modify the HUD queue call in `plugins/hub/plugin.py`:

```python
pending_replies = []
if self._task_ledger:
    pending_replies = self._task_ledger.pending_replies()
if pending_replies:
    llm_service.queue_agent_hud(
        section="hub",
        label="pending replies",
        content="\n".join(
            f"{item['assignee']}: {item['task_id']}"
            for item in pending_replies[:8]
        ),
    )
```

- [ ] **Step 7: Run pending reply tests**

Run:

```bash
python -m pytest tests/unit/test_hub_pending_replies.py tests/unit/test_hub_msg_parsing.py tests/unit/test_hub_wake_order.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 8: Commit**

Run:

```bash
git add plugins/hub/task_ledger.py plugins/hub/session_state.py plugins/hub/plugin.py tests/unit/test_hub_pending_replies.py
git commit -m "feat: track expected hub replies"
```

Expected:

```text
commit created with no attribution footer
```

---

### Task 6: Add Runtime Smoke Coverage

**Files:**

- Create or Modify: `tests/integration/test_hub_delivery_smoke.py`
- Modify: `scripts/stabilization-gate.sh`

- [ ] **Step 1: Add smoke test shell harness**

Create `tests/integration/test_hub_delivery_smoke.py`:

```python
import subprocess
import sys
from pathlib import Path


def test_hub_delivery_smoke_script_exists():
    script = Path("scripts/stabilization-gate.sh")
    assert script.exists()
    assert "test_hub_delivery_policy.py" in script.read_text()
```

- [ ] **Step 2: Add focused hub tests to the gate**

Modify `scripts/stabilization-gate.sh` by adding the new files to the hub test section:

```bash
tests/unit/test_hub_dns_liveness.py
tests/unit/test_hub_delivery_policy.py
tests/unit/test_hub_delivery_trace.py
tests/unit/test_hub_remote_trust.py
tests/unit/test_hub_pending_replies.py
```

- [ ] **Step 3: Run gate**

Run:

```bash
./scripts/stabilization-gate.sh
```

Expected:

```text
all tests pass
```

- [ ] **Step 4: Commit**

Run:

```bash
git add tests/integration/test_hub_delivery_smoke.py scripts/stabilization-gate.sh
git commit -m "test: add hub delivery stabilization coverage"
```

Expected:

```text
commit created with no attribution footer
```

---

### Task 7: Document The New Hub DNS Model

**Files:**

- Modify: `docs/architecture/reference/agent-dns-reference.md`
- Modify: `docs/architecture/README.md`

- [ ] **Step 1: Add the reference model**

Add this section to `docs/architecture/reference/agent-dns-reference.md`:

```markdown
## Trust, Liveness, And Delivery Boundaries

Agent DNS is the durable identity and trust registry. It is not the sole
delivery path and it must not delete trust decisions because a runtime process
went offline.

- identity: stable designation, authority, public key, and capabilities
- trust: approval state, rejection state, trust score, endorsements
- liveness: current socket or endpoint freshness
- delivery: policy result plus socket, mailbox, or remote transport
- wake: independent decision about whether a message should enter the LLM

Approved and rejected trust records survive liveness refresh. Liveness refresh
may mark an endpoint stale, but it cannot silently erase approval state.

Remote agents must use signed envelopes. Unknown remote agents are quarantined
until explicitly approved. Local same-project agents are allowed by default
with a warning when DNS freshness is missing, unless strict local mode is on.
```

- [ ] **Step 2: Add troubleshooting commands**

Add:

````markdown
## Troubleshooting Delivery

Use these checks when a worker says it reported back but koordinator did not
wake:

```bash
rg "Message from .* blocked|queued_identity_mailbox|quarantined|socket_send_failed" ~/.kollab -g "*.log" -g "*.jsonl"
python -m pytest tests/unit/test_hub_dns_liveness.py tests/unit/test_hub_delivery_policy.py tests/unit/test_hub_pending_replies.py -q
```

The important question is not "did the peer speak?" It is:

1. was the message created?
2. was the sender policy accepted, rejected, or quarantined?
3. was the message socket-sent or identity-mailbox queued?
4. was the message injected into the recipient's HUD or LLM turn?
5. did wake classification decide `wake`, `observe`, or `buffer`?
````

- [ ] **Step 3: Update architecture README**

Add a reference link in `docs/architecture/README.md`:

```markdown
- [Agent DNS Trust and Delivery](reference/agent-dns-reference.md)
  documents identity, trust, liveness, remote envelopes, durable mailboxes,
  delivery traces, and wake boundaries.
```

- [ ] **Step 4: Commit docs**

Run:

```bash
git add docs/architecture/reference/agent-dns-reference.md docs/architecture/README.md
git commit -m "docs: document hub dns trust and delivery model"
```

Expected:

```text
commit created with no attribution footer
```

---

## Full Verification

After all tasks:

```bash
python -m pytest tests/unit/test_hub_dns_liveness.py tests/unit/test_hub_delivery_policy.py tests/unit/test_hub_delivery_trace.py tests/unit/test_hub_remote_trust.py tests/unit/test_hub_pending_replies.py -q
python -m pytest tests/unit/test_hub_msg_parsing.py tests/unit/test_hub_wake_order.py tests/unit/test_hub_mesh_force.py tests/unit/test_hub_identity_mailbox.py tests/unit/test_ghost_response.py -q
python -m ruff check plugins/hub packages/kollabor-ai/src tests/unit/test_hub_dns_liveness.py tests/unit/test_hub_delivery_policy.py tests/unit/test_hub_delivery_trace.py tests/unit/test_hub_remote_trust.py tests/unit/test_hub_pending_replies.py
./scripts/stabilization-gate.sh
```

Expected:

```text
all tests pass
ruff reports no issues
stabilization gate passes
```

## Manual Smoke

Run this after the test suite:

```bash
kollab --hub stop all
kollab
```

In another terminal, start three agents and detach.

Ask koordinator:

```text
tell lapis, sapphire, and aquamarine to review the hub delivery patch and report back.
give me a consolidated report in 2 minutes.
```

Expected:

```text
each worker receives the assignment
each worker sends a final report
koordinator receives the final reports
koordinator wakes once or buffers once
koordinator gives the consolidated report
pure standing-by acknowledgements display in HUD/logs but do not create loops
```

Then simulate restart:

```text
stop koordinator after sending assignments
restart koordinator
start workers
ask koordinator for pending reports
```

Expected:

```text
pending reply HUD shows unresolved assignments
identity mailbox delivers queued direct reports
delivery trace explains any rejected or quarantined message
```

## Commit Order

Use these commit messages exactly:

```text
fix: preserve hub delivery through stale dns state
feat: add explicit hub delivery policy
feat: separate hub dns trust from liveness
feat: add durable hub delivery trace
feat: add remote hub envelope verification contract
feat: track expected hub replies
test: add hub delivery stabilization coverage
docs: document hub dns trust and delivery model
```

No commit footers.

## Self-Review

Spec coverage:

- ghost responses: covered in Task 0
- stable identity mailbox: covered in Task 0 and Task 3
- DNS liveness not deleting trust: covered in Task 2
- delivery/wake separation: covered in Task 1 and Task 5
- remote agents on other servers: covered in Task 4
- traceability and timestamps: covered in Task 3
- raw troubleshooting evidence: covered in Task 7
- stabilization gate: covered in Task 6

Risk notes:

- Remote envelope verification starts as a contract scaffold. Full Ed25519 validation needs a follow-up slice once the canonical key library is chosen.
- Local unknown senders default to delivery with warning because Marco prefers responsiveness over dropped reports. Strict mode remains available for locked-down environments.
- The current giant `plugins/hub/plugin.py` is still too big. This plan extracts the delivery decision first without doing a risky hub rewrite.
