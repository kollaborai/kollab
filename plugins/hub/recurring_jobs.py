"""Recurring hub jobs (#728): opt-in messages the coordinator sends on a schedule.

Each job is a prompt sent to one or more agents every ``schedule``. Everything
repo-specific (prompt, schedule, targets) comes from
``plugins.hub.hub_jobs.<name>``; nothing here names a repository, a check or an
agent. Every job ships disabled.

Guardrails, all enforced here rather than left to the prompt text:

* the job rules ("never merge, approve, close, push or restart") are appended
  to every prompt, including a custom one;
* the last run and the recent sends are kept in a state file, so a restart does
  not fire a job early or reset its message cap;
* a job still sending is skipped, not queued behind itself;
* ``max_messages_per_hour`` caps the sends of one job.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

JOB_RULES = (
    "Job rules: never merge, approve, close, push or restart anything. "
    "Report what you find and ask the operator for those actions."
)
MIN_SCHEDULE_SECONDS = 60.0
SEND_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_MESSAGES_PER_HOUR = 6
_WINDOW_SECONDS = 3600.0

# name -> (default schedule, default prompt). All five default to the
# coordinator; the operator points them elsewhere in config.
JOB_DEFAULTS: Dict[str, Dict[str, str]] = {
    "koordinator_refresh": {
        "schedule": "30m",
        "prompt": (
            "Refresh your session: re-read the work queue and each lane's "
            "latest status, and flag any lane that has stalled."
        ),
    },
    "idle_sweep": {
        "schedule": "15m",
        "prompt": (
            "Idle sweep: list the agents that are idle while their lane still "
            "has open work, and wake each of them with one message."
        ),
    },
    "re_review": {
        "schedule": "30m",
        "prompt": (
            "Re-review check: find pull requests with open review findings "
            "that have new commits since the last review, and ask for a "
            "fresh review of each."
        ),
    },
    "kpi_rebuild": {
        "schedule": "1h",
        "prompt": (
            "Rebuild the KPI dashboard and its artifact pages from current "
            "data, and report anything that changed materially."
        ),
    },
    "hub_heartbeat": {
        "schedule": "5m",
        "prompt": (
            "Hub heartbeat: check for undelivered messages and for agents "
            "that have gone offline, and report any you find."
        ),
    },
}


def default_job_config() -> Dict[str, Dict[str, Any]]:
    """The ``plugins.hub.hub_jobs`` defaults: every job off."""
    return {
        name: {
            "enabled": False,
            "schedule": spec["schedule"],
            "target": "koordinator",
            "prompt": spec["prompt"],
            "max_messages_per_hour": DEFAULT_MAX_MESSAGES_PER_HOUR,
        }
        for name, spec in JOB_DEFAULTS.items()
    }


def parse_schedule(text: str) -> float:
    """Seconds in ``30m``, ``1h``, ``2h30m``; ValueError when unusable."""
    match = re.fullmatch(
        r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", str(text).strip().lower()
    )
    if not match or not any(match.groups()):
        raise ValueError(f"invalid schedule {text!r} (use e.g. 30m, 1h, 2h30m)")
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    total = float(hours * 3600 + minutes * 60 + seconds)
    if total < MIN_SCHEDULE_SECONDS:
        raise ValueError(f"schedule {text!r} is under {int(MIN_SCHEDULE_SECONDS)}s")
    return total


def _targets(value: Any) -> List[str]:
    items = value if isinstance(value, (list, tuple)) else [value]
    return [str(item).strip() for item in items if str(item or "").strip()]


class RecurringJobs:
    """Runs the configured jobs. One instance per coordinator process."""

    def __init__(
        self,
        config_get: Callable[[str, Any], Any],
        send: Callable[[str, str, str], Awaitable[bool]],
        state_path: Path,
    ) -> None:
        self._get = config_get
        self._send = send
        self._state_path = Path(state_path)
        self._state: Optional[Dict[str, Dict[str, Any]]] = None
        self._running: set[str] = set()
        self._warned: set[str] = set()

    def _read_state(self) -> Dict[str, Dict[str, Any]]:
        if self._state is None:
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._state = data if isinstance(data, dict) else {}
            except OSError, ValueError:
                self._state = {}
        return self._state

    def _write_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._state_path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._read_state(), handle)
            os.replace(tmp, self._state_path)
        except OSError as exc:
            logger.warning("hub jobs: could not save state (%s)", exc)

    def _setting(self, name: str, key: str) -> Any:
        return self._get(
            f"plugins.hub.hub_jobs.{name}.{key}", default_job_config()[name][key]
        )

    async def tick(self, now: float) -> List[str]:
        """Run every due job once. Returns the names that sent something."""
        due = [name for name in JOB_DEFAULTS if self._is_due(name, now)]
        results = await asyncio.gather(*(self.run_job(n, now) for n in due))
        return [name for name, sent in zip(due, results) if sent]

    def _is_due(self, name: str, now: float) -> bool:
        if self._setting(name, "enabled") is not True:
            return False
        try:
            interval = parse_schedule(self._setting(name, "schedule"))
        except ValueError as exc:
            if name not in self._warned:
                self._warned.add(name)
                logger.warning("hub job %s not run: %s", name, exc)
            return False
        self._warned.discard(name)
        entry = self._read_state().setdefault(name, {})
        last = entry.get("last_run")
        if not isinstance(last, (int, float)) or last > now:
            # First sight of an enabled job (or a clock that went backwards)
            # starts the clock; the first send is one schedule away.
            entry["last_run"] = now
            self._write_state()
            return False
        return now - last >= interval

    async def run_job(self, name: str, now: float) -> bool:
        """Send one job's prompt to its targets. True when anything was sent."""
        if name in self._running:
            logger.info("hub job %s skipped: previous run still going", name)
            return False
        self._running.add(name)
        try:
            return await self._run(name, now)
        finally:
            self._running.discard(name)

    async def _run(self, name: str, now: float) -> bool:
        entry = self._read_state().setdefault(name, {})
        entry["last_run"] = now  # set first: a failed run waits a full interval
        recent = [
            t
            for t in entry.get("sent", [])
            if isinstance(t, (int, float)) and now - _WINDOW_SECONDS < t <= now
        ]
        try:
            cap = max(0, int(self._setting(name, "max_messages_per_hour")))
        except TypeError, ValueError:
            cap = DEFAULT_MAX_MESSAGES_PER_HOUR
        prompt = str(self._setting(name, "prompt") or JOB_DEFAULTS[name]["prompt"])
        content = f"{prompt.strip()}\n\n{JOB_RULES}"

        sent_any = False
        error = ""
        for target in _targets(self._setting(name, "target")):
            if len(recent) >= cap:
                error = f"message cap of {cap} per hour reached"
                break
            try:
                delivered = await asyncio.wait_for(
                    self._send(name, target, content), SEND_TIMEOUT_SECONDS
                )
            except Exception as exc:
                error = f"send to {target} failed ({type(exc).__name__})"
                continue
            if delivered:
                recent.append(now)
                sent_any = True
            else:
                error = f"{target} did not accept the message"
        entry["sent"] = recent
        entry["last_error"] = error
        self._write_state()
        if error:
            logger.warning("hub job %s: %s", name, error)
        return sent_any
