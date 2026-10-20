"""Idle supervisor: open hub work must never idle past the wake cooldown.

Issue #211: agents and the koordinator go idle while work sits queued.
Agents only act when a message or cron fires, and a finished worker reports
to a koordinator that itself ends its turn — so open work could sit for
hours until an operator nudged someone.

This module holds the supervisor's decision logic. ``evaluate`` is a pure
function of (ledger, roster, now, config, state) -> decisions with ONE
documented side effect: stalled cards past their nudge grace are requeued
on the ledger itself, so the wake text always matches disk state. The hub
plugin runs it on a poll loop (coordinator only) and delivers decisions
through the existing wake paths: a self-targeted message re-invokes the
local coordinator the same way self-targeted cron does, and remote peers
get ``_deliver_to_agent``.

Acceptance (issue #211): a hub with open ledger items never stays idle
past the wake cooldown (~2 minutes by default, poll every 10s) without an
operator nudge.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_POLL_INTERVAL = 10.0
DEFAULT_STALL_THRESHOLD = 900.0
"""Seconds of card inactivity before a worker is nudged (15m default)."""

DEFAULT_WAKE_COOLDOWN = 120.0
"""Minimum seconds between coordinator wakes, so one poll pass per burst."""

DEFAULT_NUDGE_GRACE = 300.0
"""Seconds a worker has to respond after a stall nudge before requeue."""

DEFAULT_MAX_REASSIGNMENTS = 3
"""A card requeued this many times is reported as blocked, not requeued."""

# Presence states from which a direct coordinator-originated message can
# re-invoke an agent. "waiting" agents accept coordinator delivery, which
# is exactly what the supervisor is.
WAKEABLE_STATES = frozenset(
    {"idle", "ready", "registered", "booting", "waiting", "unknown"}
)

DEAD_STATES = frozenset({"dead", "disconnecting", "disconnected"})


@dataclass
class SupervisorDecision:
    """One action the hub plugin should take for the supervisor."""

    kind: str  # wake_coordinator | nudge_worker | requeue_stalled
    card_id: str = ""
    worker: str = ""  # coordinator identity for wakes, assignee for nudges
    text: str = ""


@dataclass
class SupervisorState:
    """Cooldown bookkeeping carried between evaluate() calls."""

    last_wake: float = 0.0
    nudges: Dict[str, float] = field(default_factory=dict)


def _online(roster: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return [
        a for a in (roster or []) if str(a.get("state", "")).lower() not in DEAD_STATES
    ]


def _pick_coordinator(
    roster: Optional[List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Prefer an idle coordinator (it can be woken cheapest); else any."""
    coords = [a for a in _online(roster) if a.get("is_coordinator")]
    for coord in coords:
        if str(coord.get("state", "")).lower() in WAKEABLE_STATES:
            return coord
    return coords[0] if coords else None


def _idle_workers(
    roster: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Worker identities that could take a card right now."""
    out = []
    for a in _online(roster):
        identity = str(a.get("identity", "") or "").strip()
        if not identity or a.get("is_coordinator"):
            continue
        if str(a.get("state", "")).lower() in ("idle", "ready"):
            out.append(identity)
    return sorted(out)


def _card_line(card) -> str:
    title = " ".join(str(card.directive or "").split())[:70]
    return f"  [{card.id}] (p{card.priority}) {title}"


def evaluate(
    ledger,
    roster: Optional[List[Dict[str, Any]]],
    now: float,
    *,
    stall_threshold: float = DEFAULT_STALL_THRESHOLD,
    wake_cooldown: float = DEFAULT_WAKE_COOLDOWN,
    nudge_grace: float = DEFAULT_NUDGE_GRACE,
    max_reassignments: int = DEFAULT_MAX_REASSIGNMENTS,
    state: Optional[SupervisorState] = None,
) -> Tuple[List[SupervisorDecision], SupervisorState]:
    """Decide what the supervisor should do this pass.

    Decisions in order:
      nudge_worker      — first stall of an active card whose worker is
                          online; one nudge per stall window.
      requeue_stalled   — card still stalled after its nudge grace (or
                          worker offline): back to the open queue,
                          unassigned. Done on the ledger here so the wake
                          text below reflects it; the plugin only logs this
                          decision.
      wake_coordinator  — open or stalled work exists and the coordinator
                          is idle, throttled by ``wake_cooldown``.

    Returns (decisions, next_state). ``state`` may be shared/mutated.
    """
    state = state if state is not None else SupervisorState()
    decisions: List[SupervisorDecision] = []

    open_cards = ledger.get_open()
    stalled_cards = ledger.get_stalled(stall_threshold, now=now)

    # Nudge records for cards that recovered (checkpoint arrived) are
    # stale: a fresh stall must earn a fresh nudge.
    stalled_ids = {c.id for c in stalled_cards}
    nudges = {cid: ts for cid, ts in state.nudges.items() if cid in stalled_ids}

    requeued: List[Any] = []
    blocked: List[Any] = []

    for card in stalled_cards:
        worker = str(card.assignee or "").strip()
        worker_presence = next(
            (a for a in _online(roster) if a.get("identity") == worker), None
        )
        worker_online = worker_presence is not None
        worker_working = (
            worker_presence is not None
            and str(worker_presence.get("state", "")).lower() == "working"
        )
        ref = card.last_activity_at or card.updated_at
        # A working worker may be in a long turn. Give it up to four stall
        # windows before treating the card as abandoned.
        if worker_working and now - ref <= 4 * stall_threshold:
            continue

        nudged_at = nudges.get(card.id)
        if worker and worker_online and nudged_at is None:
            # First stall: give the worker one nudge before taking the
            # card back. The nudge itself re-invokes the worker.
            minutes = max(1, int(stall_threshold / 60))
            decisions.append(
                SupervisorDecision(
                    kind="nudge_worker",
                    card_id=card.id,
                    worker=worker,
                    text=(
                        f"[idle-supervisor] task {card.id} has had no"
                        f" activity for over {minutes}m. Report progress"
                        f' with <task_checkpoint id="{card.id}">note'
                        "</task_checkpoint> or finish with"
                        f' <task_complete id="{card.id}">result'
                        "</task_complete>. If you are stuck, say so —"
                        " otherwise the card will be requeued to the"
                        " open queue for reassignment."
                    ),
                )
            )
            nudges[card.id] = now
        elif (
            worker
            and worker_online
            and nudged_at is not None
            and now - nudged_at < nudge_grace
        ):
            # The worker has a real grace window to receive and answer the
            # nudge; a later poll must not churn the assignment.
            continue
        elif card.reassignments < max_reassignments:
            fresh = ledger.requeue_stalled(
                card.id,
                reason=(
                    "worker offline since claim"
                    if not worker_online
                    else "stalled past nudge grace"
                ),
            )
            if fresh is not None:
                decisions.append(
                    SupervisorDecision(
                        kind="requeue_stalled",
                        card_id=card.id,
                        worker=worker,
                        text=_card_line(fresh),
                    )
                )
                requeued.append(fresh)
                nudges.pop(card.id, None)
        else:
            blocked.append(card)

    needs_wake = bool(open_cards or requeued or blocked)
    wake_decision: Optional[SupervisorDecision] = None
    if needs_wake:
        coord = _pick_coordinator(roster)
        if coord is not None and now - state.last_wake >= wake_cooldown:
            lines: List[str] = ["[idle-supervisor] open work on the ledger."]
            if open_cards:
                lines.append(f"open cards ({len(open_cards)}):")
                lines.extend(_card_line(c) for c in open_cards)
            if requeued:
                lines.append(f"requeued after stall ({len(requeued)}):")
                lines.extend(_card_line(c) for c in requeued)
            if blocked:
                lines.append(
                    f"blocked at max reassignments ({len(blocked)}):"
                    " needs human/coordinator review"
                )
                lines.extend(_card_line(c) for c in blocked)
            idle = _idle_workers(roster)
            if idle:
                lines.append(f"idle workers: {', '.join(idle)}")
            lines.append(
                "assign before ending your turn:"
                ' <hub_msg to="worker" task_id="card-id">directive'
                "</hub_msg> claims the open card for that worker,"
                ' or claim it yourself with <hub_claim id="card-id"/>.'
            )
            wake_decision = SupervisorDecision(
                kind="wake_coordinator",
                worker=str(coord.get("identity", "") or ""),
                text="\n".join(lines),
            )
            decisions.append(wake_decision)
            state.last_wake = now
            state.nudges = nudges
            return decisions, state

    state.nudges = nudges
    return decisions, state
