"""Usage ledger plugin: token and cost per agent and per lane (issue #723).

Off by default (``plugins.usage_ledger.enabled``). When on, every finished LLM
turn appends one row to ``<project data dir>/usage/ledger.jsonl``. The row holds
counts and metadata only, never prompts or outputs. Logic lives in
``kollabor_ai.usage_ledger``; this plugin only wires it to the event bus and
the ``/usage`` command.

Lane and PR labels come from ``KOLLAB_USAGE_LANE`` / ``KOLLAB_USAGE_PR`` or
``plugins.usage_ledger.lane`` / ``.pr``. Set them per process to attribute a run.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from kollabor_ai.usage_ledger import (
    COST_UNPRICED,
    LEDGER_FILENAME,
    USAGE_NONE,
    UsageLedger,
    budget_alerts,
    build_turn_record,
    rollup,
)
from kollabor_config.config_utils import get_project_data_dir
from kollabor_events import EventType, Hook, HookPriority
from kollabor_events.models import CommandCategory, CommandDefinition, SubcommandInfo
from kollabor_plugins import BasePlugin

logger = logging.getLogger(__name__)

CONFIG_PREFIX = "plugins.usage_ledger"
PRUNE_INTERVAL_S = 3600


class UsageLedgerPlugin(BasePlugin):
    """Records one ledger row per LLM turn and reports rollups via /usage."""

    def __init__(self, name: str, event_bus: Any, renderer: Any, config: Any) -> None:
        self.name = name
        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config
        self._command_registry = None
        self._agent_manager = None
        self._ledger: Optional[UsageLedger] = None
        self._turn_started: Optional[float] = None
        self._last_prune = 0.0

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        return {
            "plugins": {
                "usage_ledger": {
                    "enabled": False,
                    "retention_days": 30,
                    "price_table": {},
                    "budget_alerts": [],
                    "lane": "",
                    "pr": "",
                }
            }
        }

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, args=None, **kwargs) -> None:
        if kwargs.get("renderer") is not None:
            self.renderer = kwargs["renderer"]
        self._command_registry = kwargs.get("command_registry")
        self._agent_manager = kwargs.get("agent_manager")

        if self._enabled():
            self._ledger = UsageLedger(
                get_project_data_dir() / "usage" / LEDGER_FILENAME,
                retention_days=self._config_int("retention_days", 30),
            )
            try:
                self._ledger.prune()
                self._last_prune = time.time()
            except Exception:
                logger.warning("usage ledger prune failed", exc_info=True)

        self._register_command()

    async def register_hooks(self) -> None:
        if not self._enabled():
            logger.info("Usage ledger disabled; no hooks registered")
            return

        pre_hook = Hook(
            name="usage_ledger_pre",
            plugin_name=self.name,
            event_type=EventType.LLM_REQUEST_PRE,
            priority=HookPriority.PREPROCESSING.value,
            callback=self._on_request_pre,
        )
        post_hook = Hook(
            name="usage_ledger_post",
            plugin_name=self.name,
            event_type=EventType.LLM_REQUEST_POST,
            priority=HookPriority.POSTPROCESSING.value,
            callback=self._on_request_post,
        )
        error_hook = Hook(
            name="usage_ledger_error",
            plugin_name=self.name,
            event_type=EventType.LLM_REQUEST_ERROR,
            priority=HookPriority.POSTPROCESSING.value,
            callback=self._on_request_error,
        )
        await self.event_bus.register_hook(pre_hook)
        await self.event_bus.register_hook(post_hook)
        await self.event_bus.register_hook(error_hook)

    async def shutdown(self) -> None:
        self._turn_started = None

    # ------------------------------------------------------------------
    # capture
    # ------------------------------------------------------------------

    async def _on_request_pre(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Start the wall clock at the first request of a turn."""
        if self._turn_started is None:
            self._turn_started = time.monotonic()
        return data

    async def _on_request_error(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """A failed request writes no row, so drop its clock."""
        self._turn_started = None
        return data

    async def _on_request_post(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Append the finished turn and evaluate budget alerts."""
        try:
            self._record_turn(data if isinstance(data, dict) else {})
        except Exception:
            # Usage accounting must never break a turn.
            logger.warning("usage ledger failed to record turn", exc_info=True)
        return data

    def _record_turn(self, data: Dict[str, Any]) -> None:
        ledger = self._get_ledger()
        if ledger is None:
            self._turn_started = None
            return

        wall_ms = None
        if self._turn_started is not None:
            wall_ms = int((time.monotonic() - self._turn_started) * 1000)
        self._turn_started = None

        usage_source = str(data.get("usage_source") or USAGE_NONE)
        price_table = self._config_value("price_table", {})
        if not isinstance(price_table, dict):
            price_table = {}

        now = time.time()
        row = build_turn_record(
            ts=now,
            agent=self._agent_name(),
            lane=self._label("lane", "KOLLAB_USAGE_LANE"),
            pr=self._label("pr", "KOLLAB_USAGE_PR"),
            provider=str(data.get("provider") or ""),
            model=str(data.get("model") or "unknown"),
            usage_source=usage_source,
            input_tokens=data.get("input_tokens"),
            output_tokens=data.get("output_tokens"),
            cache_read_tokens=data.get("cache_read_tokens"),
            cache_write_tokens=data.get("cache_creation_tokens"),
            wall_ms=wall_ms,
            price_table=price_table,
        )
        ledger.append(row)

        if now - self._last_prune >= PRUNE_INTERVAL_S:
            self._last_prune = now
            try:
                ledger.prune(now)
            except Exception:
                logger.warning("usage ledger prune failed", exc_info=True)

        rules = self._config_value("budget_alerts", [])
        if isinstance(rules, list) and rules:
            for alert in budget_alerts(ledger.read(), rules, now):
                ledger.append(alert)
                self._show_alert(alert)

    # ------------------------------------------------------------------
    # reporting
    # ------------------------------------------------------------------

    def _register_command(self) -> None:
        if not self._command_registry:
            return
        cmd = CommandDefinition(
            name="usage",
            description="Token and cost usage per agent and lane",
            plugin_name=self.name,
            category=CommandCategory.SYSTEM,
            handler=self._handle_usage_command,
            subcommands=[
                SubcommandInfo("json", "", "Show the full rollup as JSON"),
            ],
        )
        self._command_registry.register_command(cmd)

    async def _handle_usage_command(self, command) -> str:
        if not self._enabled():
            return (
                "Usage ledger is off. Set plugins.usage_ledger.enabled to true "
                "and restart to record usage."
            )
        ledger = self._get_ledger()
        if ledger is None:
            return "Usage ledger is unavailable."

        rows = ledger.read()
        args = getattr(command, "args", []) or []
        if args and str(args[0]).lower() == "json":
            return json.dumps(self._summary(rows, ledger.path), indent=2)
        return _format_text(self._summary(rows, ledger.path))

    def _summary(self, rows: List[Dict[str, Any]], path) -> Dict[str, Any]:
        turns = [r for r in rows if r.get("kind") == "turn"]
        return {
            "ledger": str(path),
            "retention_days": self._config_int("retention_days", 30),
            "turns": len(turns),
            "turns_without_usage": sum(1 for r in turns if r.get("tokens_in") is None),
            "unpriced_turns": sum(
                1 for r in turns if r.get("cost_status") == COST_UNPRICED
            ),
            "by_agent": rollup(turns, ("agent",)),
            "by_lane": rollup(turns, ("lane",)),
            "by_lane_agent": rollup(turns, ("lane", "agent")),
            "by_lane_agent_model": rollup(turns, ("lane", "agent", "model_key")),
            "by_model": rollup(turns, ("model_key",)),
            "by_pr": rollup(turns, ("pr",)),
            "by_day": rollup(turns, ("day",)),
            "budget_alerts": [r for r in rows if r.get("kind") == "budget_alert"],
        }

    def _show_alert(self, alert: Dict[str, Any]) -> None:
        text = (
            f"Budget alert: {alert['scope']} '{alert['name']}' reached "
            f"${alert['cost_usd']:.2f} (threshold ${alert['threshold_usd']:.2f} "
            f"in a {alert['window_hours']:g}h window)."
        )
        if alert.get("unpriced_turns"):
            text += f" {alert['unpriced_turns']} unpriced turn(s) not counted."
        logger.warning(text)

        renderer = self.renderer
        if renderer is None or getattr(renderer, "pipe_mode", False) is True:
            return
        coordinator = getattr(renderer, "message_coordinator", None)
        display = getattr(coordinator, "display_message_sequence", None)
        if not callable(display):
            return
        try:
            display([("system", text, {"display_type": "warning"})])
        except Exception:
            logger.warning("Failed to display usage budget alert", exc_info=True)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _enabled(self) -> bool:
        return bool(self._config_value("enabled", False))

    def _get_ledger(self) -> Optional[UsageLedger]:
        return self._ledger if self._enabled() else None

    def _config_value(self, key: str, default: Any) -> Any:
        if self.config is None:
            return default
        try:
            value = self.config.get(f"{CONFIG_PREFIX}.{key}", default)
        except Exception:
            return default
        return default if value is None else value

    def _config_int(self, key: str, default: int) -> int:
        try:
            return int(self._config_value(key, default))
        except TypeError, ValueError:
            return default

    def _label(self, key: str, env_name: str) -> str:
        env_value = os.environ.get(env_name, "").strip()
        if env_value:
            return env_value
        return str(self._config_value(key, "") or "").strip()

    def _agent_name(self) -> str:
        """The hub identity when on a hub, else the active agent's name."""
        hub = None
        if self.event_bus is not None:
            try:
                hub = self.event_bus.get_service("hub_plugin")
            except Exception:
                hub = None
        identity = getattr(hub, "_identity", None) if hub is not None else None
        name = str(getattr(identity, "name", "") or "")
        if not name and self._agent_manager is not None:
            name = str(getattr(self._agent_manager, "active_agent_name", "") or "")
        return name or "default"


def _money(value: Optional[float], turns: int, unpriced: int) -> str:
    if turns and unpriced >= turns:
        return "unpriced"
    if value is None:
        return "n/a"
    return f"${value:.4f}"


def _count(value: Optional[int]) -> str:
    return "n/a" if value is None else f"{value:,}"


def _format_text(summary: Dict[str, Any]) -> str:
    lines = [
        f"Usage ledger: {summary['ledger']}",
        (
            f"Turns: {summary['turns']}  |  no usage reported: "
            f"{summary['turns_without_usage']}  |  unpriced: "
            f"{summary['unpriced_turns']}  |  retention: "
            f"{summary['retention_days']}d"
        ),
    ]
    if not summary["turns"]:
        lines.append("No turns recorded yet.")
        return "\n".join(lines)

    def table(title: str, groups: List[Dict[str, Any]], fields: List[str]) -> None:
        lines.append("")
        lines.append(title)
        header = (
            f"  {' / '.join(fields):<28} {'turns':>6} {'in':>12} {'out':>12} "
            f"{'cache rd':>12} {'cost':>12} {'no usage':>9}"
        )
        lines.append(header)
        for g in groups:
            label = " / ".join(str(g.get(f) or "(none)") for f in fields)
            lines.append(
                f"  {label[:28]:<28} {g['turns']:>6} "
                f"{_count(g['tokens_in']):>12} {_count(g['tokens_out']):>12} "
                f"{_count(g['cache_read_tokens']):>12} "
                f"{_money(g['cost_usd'], g['turns'], g['unpriced_turns']):>12} "
                f"{g['turns_without_usage']:>9}"
            )

    table("By agent", summary["by_agent"], ["agent"])
    if any(g["lane"] != "(none)" for g in summary["by_lane_agent"]):
        table("By lane and agent", summary["by_lane_agent"], ["lane", "agent"])
    table("By model", summary["by_model"], ["model_key"])
    if any(g["pr"] != "(none)" for g in summary["by_pr"]):
        table("By PR", summary["by_pr"], ["pr"])
    if summary["budget_alerts"]:
        lines.append("")
        lines.append(f"Budget alerts recorded: {len(summary['budget_alerts'])}")
    lines.append("")
    lines.append("Run /usage json for the full rollup.")
    return "\n".join(lines)
