"""Event hooks that wake agents (issue #727).

A pure decision engine: ``WakeHookEngine.handle(event, now)`` returns the
wakes, alerts and re-review requests the hub plugin should deliver, and
nothing else. Where events come from (a review verdict, a merge, a missed
heartbeat) and how actions are delivered stay with the caller, so the rules
below are the same for every source and every repo.

Rules, all from the issue:

* Every hook is opt-in and off by default (``plugins.hub.hooks.<name>``).
* One event key per (hook, PR, head SHA, ref, agent): a repeat does nothing.
* Per-hook cooldown per target and a rolling hourly cap on actions.
* A target never hears about an event it caused (``WakeEvent.actor``).
* A hook never triggers another hook: events marked ``from_hook`` are dropped.
* Repo, check names, agents and schedules come from config or the event,
  never from this file.
"""

from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

HOOK_NAMES = (
    "review_ready",
    "dependency_clear",
    "idle_nudge",
    "auto_rereview",
    "undelivered_messages",
    "koordinator_heartbeat",
)

CONFIG_ROOT = "plugins.hub.hooks"
HOUR = 3600.0
_MAX_SEEN = 1024

# name -> (default targets, action kind, cooldown seconds, wakes per hour,
# threshold minutes or None when the event alone is the trigger)
_HOOKS: Dict[str, Tuple[List[str], str, float, int, Optional[float]]] = {
    "review_ready": (["@coder", "@koordinator"], "wake", 60.0, 20, None),
    "dependency_clear": (["@dependents"], "wake", 60.0, 20, None),
    "idle_nudge": (["@agent"], "wake", 900.0, 4, 15.0),
    "auto_rereview": ([], "rereview", 60.0, 20, None),
    "undelivered_messages": (["@koordinator", "@owner"], "wake", 300.0, 12, 5.0),
    "koordinator_heartbeat": ([], "alert", 900.0, 4, 10.0),
}


@dataclass(frozen=True)
class WakeEvent:
    """Something that happened. ``name`` is the hook it can trigger."""

    name: str
    pr: str = ""
    sha: str = ""
    ref: str = ""  # discriminator for events with no PR/head (message id, idle-since)
    actor: str = ""  # agent whose action caused the event
    agent: str = ""  # the agent the event is about (idle, offline, no heartbeat)
    coder: str = ""
    koordinator: str = ""
    owner: str = ""
    dependents: Tuple[str, ...] = ()
    age_seconds: float = 0.0
    open_work: bool = False
    open_findings: bool = False
    detail: str = ""
    from_hook: bool = False  # sent by a hook: never starts another


@dataclass(frozen=True)
class WakeAction:
    """One delivery the plugin should make."""

    hook: str
    target: str  # "" for an alert with no configured recipient (log only)
    kind: str  # wake | rereview | alert
    text: str


def default_hook_config() -> Dict[str, Dict[str, Any]]:
    """Defaults for ``plugins.hub.hooks``: every hook off."""
    out: Dict[str, Dict[str, Any]] = {}
    for name, (targets, _, cooldown, per_hour, minutes) in _HOOKS.items():
        entry: Dict[str, Any] = {
            "enabled": False,
            "targets": list(targets),
            "cooldown": cooldown,
            "max_per_hour": per_hour,
            "thresholds": {},
        }
        if minutes is not None:
            entry["thresholds"] = {"minutes": minutes}
        out[name] = entry
    return out


def _number(value: Any, default: float, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value) if value >= minimum else default


class WakeHookEngine:
    """Applies the hook rules; holds only dedupe, cooldown and cap state."""

    def __init__(self, get_config: Callable[[str, Any], Any]) -> None:
        self._get = get_config
        self._seen: "OrderedDict[tuple, None]" = OrderedDict()
        self._last: Dict[Tuple[str, str], float] = {}
        self._sent: Dict[str, Deque[float]] = {}

    def _cfg(self, hook: str, key: str, default: Any = None) -> Any:
        return self._get(f"{CONFIG_ROOT}.{hook}.{key}", default)

    def _targets(self, hook: str, event: WakeEvent) -> List[str]:
        configured = self._cfg(hook, "targets", None)
        if isinstance(configured, str):
            configured = [configured]
        if not isinstance(configured, (list, tuple)) or not configured:
            configured = _HOOKS[hook][0]
        tokens = {
            "@coder": [event.coder],
            "@koordinator": [event.koordinator],
            "@owner": [event.owner],
            "@dependents": list(event.dependents),
            "@agent": [event.agent],
        }
        out: List[str] = []
        for item in configured:
            for name in tokens.get(item, [item]) if isinstance(item, str) else []:
                name = str(name or "").strip()
                if name and name != event.actor and name not in out:
                    out.append(name)
        return out

    def _text(self, hook: str, event: WakeEvent) -> str:
        pr = f"PR {event.pr}" if event.pr else "the task"
        head = f" at {event.sha[:8]}" if event.sha else ""
        minutes = int(event.age_seconds // 60)
        who = event.agent or "an agent"
        lines = {
            "review_ready": f"[wake-hook] A review verdict is posted on {pr}{head}.",
            "dependency_clear": f"[wake-hook] {pr} is closed or merged: work that"
            " waited on it can go on.",
            "idle_nudge": f"[wake-hook] {who} has been idle {minutes}m while its"
            " lane still has open work.",
            "auto_rereview": f"[wake-hook] A new commit{head} landed on {pr} with"
            " open findings: re-run the independent review on this head.",
            "undelivered_messages": f"[wake-hook] A message to {who} has gone"
            f" undelivered for {minutes}m (target offline or not reading).",
            "koordinator_heartbeat": f"[wake-hook] The heartbeat of {who} has been"
            f" missed for {minutes}m.",
        }
        text = lines[hook]
        return f"{text} {event.detail}".rstrip() if event.detail else text

    def handle(self, event: WakeEvent, now: float) -> List[WakeAction]:
        hook = event.name
        if hook not in _HOOKS or event.from_hook:
            return []
        if self._cfg(hook, "enabled", False) is not True:
            return []
        _, kind, cooldown_default, cap_default, minutes = _HOOKS[hook]
        if minutes is not None:
            thresholds = self._cfg(hook, "thresholds", {})
            wanted = _number(
                thresholds.get("minutes") if isinstance(thresholds, dict) else None,
                minutes,
            )
            if event.age_seconds < wanted * 60:
                return []
        if hook == "idle_nudge" and not event.open_work:
            return []
        if hook == "auto_rereview" and not event.open_findings:
            return []

        key = (hook, event.pr, event.sha, event.ref, event.agent)
        if key in self._seen:
            return []

        targets = self._targets(hook, event)
        if not targets and kind != "alert":
            return []
        if not targets:
            targets = [""]  # an alert with no recipient is still logged

        cooldown = _number(self._cfg(hook, "cooldown"), cooldown_default)
        cap = int(_number(self._cfg(hook, "max_per_hour"), cap_default, minimum=1))
        window = self._sent.setdefault(hook, deque())
        while window and now - window[0] >= HOUR:
            window.popleft()

        actions: List[WakeAction] = []
        for target in targets:
            if len(window) >= cap:
                break
            last = self._last.get((hook, target))
            if last is not None and now - last < cooldown:
                continue
            self._last[(hook, target)] = now
            window.append(now)
            actions.append(
                WakeAction(
                    hook=hook,
                    target=target,
                    kind=kind,
                    text=self._text(hook, event),
                )
            )
        if actions:
            self._seen[key] = None
            while len(self._seen) > _MAX_SEEN:
                self._seen.popitem(last=False)
        return actions
