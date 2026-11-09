"""Per-agent, per-lane token and cost ledger (issue #723).

Pure logic: pricing, record shape, JSONL storage, retention, rollups and budget
alerts. The plugin feeds it one record per LLM turn; nothing here talks to a
provider or the network.

Rules this module enforces:

- A missing provider usage report is ``None``, never zero. A turn with no
  counts is recorded with ``usage_source: "none"`` and null token fields.
- A model with no known price is ``cost_status: "unpriced"`` with a null cost.
  It is never priced at zero or guessed.
- The ledger holds counts and metadata only, never prompts or outputs.
- The file is owner-only (0600) and stays local.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from kollabor_config.file_lock import locked_file

from .pricing_registry import ModelPricing, PricingRegistry

logger = logging.getLogger(__name__)

LEDGER_FILENAME = "ledger.jsonl"
DEFAULT_RETENTION_DAYS = 30
SECONDS_PER_DAY = 86400
NO_GROUP = "(none)"

KIND_TURN = "turn"
KIND_BUDGET_ALERT = "budget_alert"

USAGE_PROVIDER = "provider"
USAGE_ESTIMATED = "estimated"
USAGE_NONE = "none"

COST_PRICED = "priced"
COST_UNPRICED = "unpriced"
COST_NO_USAGE = "no_usage"

# Keys a price_table entry may use (per million tokens, USD).
_PRICE_KEYS = ("prompt_per_million", "completion_per_million", "cache_read_per_million")


def model_key(provider: str, model: str) -> str:
    """The grouping key for a model: ``provider/model``."""
    provider = (provider or "").strip()
    model = (model or "").strip() or "unknown"
    return f"{provider}/{model}" if provider else model


def resolve_pricing(
    provider: str,
    model: str,
    price_table: Optional[Dict[str, Any]] = None,
) -> Optional[ModelPricing]:
    """Return the per-token rates for a model, or ``None`` when unpriced.

    The user's ``price_table`` wins. Keys are tried as ``provider/model`` and
    then ``model``. Otherwise the shared pricing registry answers, which already
    loads the bundled model list and ``~/.kollab/pricing.json``.
    """
    for key in (model_key(provider, model), (model or "").strip()):
        entry = (price_table or {}).get(key) if key else None
        pricing = _pricing_from_entry(entry)
        if pricing is not None:
            return pricing

    registry = PricingRegistry()
    registry.load_defaults()
    return registry.get_pricing(provider or "", model or "")


def _pricing_from_entry(entry: Any) -> Optional[ModelPricing]:
    """Build ModelPricing from a price_table entry, or None if it is invalid."""
    if not isinstance(entry, dict):
        return None
    try:
        prompt_rate = float(entry["prompt_per_million"])
        completion_rate = float(entry["completion_per_million"])
        cache_rate = float(entry.get("cache_read_per_million", prompt_rate * 0.1))
    except KeyError, TypeError, ValueError:
        logger.warning("usage_ledger: ignoring invalid price_table entry %r", entry)
        return None
    if prompt_rate < 0 or completion_rate < 0 or cache_rate < 0:
        logger.warning("usage_ledger: negative rate in price_table entry %r", entry)
        return None
    return ModelPricing(
        prompt_per_token=prompt_rate / 1_000_000,
        completion_per_token=completion_rate / 1_000_000,
        cache_discount=(cache_rate / prompt_rate) if prompt_rate > 0 else 0.5,
    )


def price_usage(
    pricing: Optional[ModelPricing],
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
) -> Optional[float]:
    """Cost in USD for one set of counts, or ``None`` when unpriced.

    Matches ``kollabor_ai.cost_calculator.calculate_cost``: a provider's prompt
    count includes its cache reads, so cached tokens bill once at the discount.
    Unlike that function, an unknown price yields ``None`` rather than ``0.0``.
    """
    if pricing is None:
        return None
    unique_prompt = max(0, prompt_tokens - cache_read_tokens)
    return (
        unique_prompt * pricing.prompt_per_token
        + completion_tokens * pricing.completion_per_token
        + cache_read_tokens * pricing.prompt_per_token * pricing.cache_discount
    )


def build_turn_record(
    *,
    ts: float,
    agent: str,
    lane: str,
    pr: str,
    provider: str,
    model: str,
    usage_source: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    cache_read_tokens: Optional[int],
    cache_write_tokens: Optional[int],
    wall_ms: Optional[int],
    price_table: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build one ledger row for a finished LLM turn.

    Token fields are null unless the provider reported them. Cost is priced
    only when the turn has counts and the model has a known price.
    """
    has_counts = usage_source != USAGE_NONE and input_tokens is not None
    if has_counts:
        pricing = resolve_pricing(provider, model, price_table)
        cost = price_usage(
            pricing,
            int(input_tokens or 0),
            int(output_tokens or 0),
            int(cache_read_tokens or 0),
        )
        cost_status = COST_PRICED if cost is not None else COST_UNPRICED
    else:
        cost = None
        cost_status = COST_NO_USAGE

    return {
        "kind": KIND_TURN,
        "ts": round(float(ts), 3),
        "day": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"),
        "agent": agent or "default",
        "lane": lane or "",
        "pr": pr or "",
        "provider": provider or "",
        "model": model or "unknown",
        "model_key": model_key(provider, model),
        "usage_source": usage_source,
        "tokens_in": int(input_tokens) if has_counts else None,
        "tokens_out": int(output_tokens or 0) if has_counts else None,
        "cache_read_tokens": int(cache_read_tokens or 0) if has_counts else None,
        "cache_write_tokens": int(cache_write_tokens or 0) if has_counts else None,
        "wall_ms": int(wall_ms) if wall_ms is not None else None,
        "cost_usd": round(cost, 8) if cost is not None else None,
        "cost_status": cost_status,
    }


class UsageLedger:
    """Append-only JSONL ledger for one project."""

    def __init__(
        self,
        path: Path,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.retention_days = max(1, int(retention_days))
        self._clock = clock

    def append(self, record: Dict[str, Any]) -> None:
        """Append one row under the file lock, creating the file 0600."""
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with locked_file(self.path, "a+", private=True) as f:
            f.write(line)
            f.flush()

    def read(self) -> List[Dict[str, Any]]:
        """Return every readable row. Corrupt lines are skipped and logged."""
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        bad = 0
        with open(self.path, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    bad += 1
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        if bad:
            logger.warning("usage_ledger: skipped %d unreadable line(s)", bad)
        return rows

    def prune(self, now: Optional[float] = None) -> int:
        """Drop rows older than the retention window. Returns rows removed."""
        if not self.path.exists():
            return 0
        cutoff = (now if now is not None else self._clock()) - (
            self.retention_days * SECONDS_PER_DAY
        )
        removed = 0
        with locked_file(self.path, "r+", private=True) as f:
            kept: List[str] = []
            for raw in f:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                    keep = float(row.get("ts", 0)) >= cutoff
                except json.JSONDecodeError, TypeError, ValueError, AttributeError:
                    keep = False
                if keep:
                    kept.append(stripped + "\n")
                else:
                    removed += 1
            if removed:
                f.seek(0)
                f.truncate()
                f.write("".join(kept))
                f.flush()
        return removed


def rollup(
    rows: Iterable[Dict[str, Any]],
    by: Sequence[str],
) -> List[Dict[str, Any]]:
    """Group turn rows by the given fields and total each group.

    ``by`` may name ``agent``, ``lane``, ``pr``, ``day``, ``model_key`` or any
    combination. Token totals sum only the turns that reported them, and
    ``turns_without_usage`` says how many did not, so a gap never reads as zero.
    Unpriced turns are counted separately and excluded from ``cost_usd``.
    """
    groups: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") != KIND_TURN:
            continue
        key = tuple(str(row.get(field) or NO_GROUP) for field in by)
        g = groups.get(key)
        if g is None:
            g = {
                **{field: value for field, value in zip(by, key)},
                "turns": 0,
                "turns_without_usage": 0,
                "unpriced_turns": 0,
                "_in": None,
                "_out": None,
                "_cache_read": None,
                "_cache_write": None,
                "cost_usd": 0.0,
                "wall_ms": 0,
            }
            groups[key] = g
        g["turns"] += 1
        if row.get("wall_ms") is not None:
            g["wall_ms"] += int(row["wall_ms"])
        if row.get("tokens_in") is None:
            g["turns_without_usage"] += 1
        else:
            for field, out in (
                ("tokens_in", "_in"),
                ("tokens_out", "_out"),
                ("cache_read_tokens", "_cache_read"),
                ("cache_write_tokens", "_cache_write"),
            ):
                value = row.get(field)
                if value is not None:
                    g[out] = (g[out] or 0) + int(value)
        if row.get("cost_status") == COST_UNPRICED:
            g["unpriced_turns"] += 1
        elif row.get("cost_usd") is not None:
            g["cost_usd"] += float(row["cost_usd"])

    out: List[Dict[str, Any]] = []
    for key in sorted(groups):
        g = groups[key]
        result = {k: v for k, v in g.items() if not k.startswith("_")}
        result["tokens_in"] = g["_in"]
        result["tokens_out"] = g["_out"]
        result["cache_read_tokens"] = g["_cache_read"]
        result["cache_write_tokens"] = g["_cache_write"]
        result["cost_usd"] = round(g["cost_usd"], 8)
        out.append(result)
    return out


def budget_alerts(
    rows: Sequence[Dict[str, Any]],
    rules: Sequence[Dict[str, Any]],
    now: float,
) -> List[Dict[str, Any]]:
    """Return new budget_alert rows whose threshold is crossed in this window.

    A rule is ``{"scope": "agent"|"lane", "name", "threshold_usd",
    "window_hours"}``. The window is fixed to the epoch, so each threshold alerts
    at most once per window. An alert already present in ``rows`` (the ledger
    is the audit log) suppresses a repeat, which also survives a restart.
    Unpriced turns add no cost and are counted in ``unpriced_turns``.
    """
    fired_keys = {r.get("key") for r in rows if r.get("kind") == KIND_BUDGET_ALERT}
    alerts: List[Dict[str, Any]] = []
    for rule in rules:
        scope = str(rule.get("scope", ""))
        name = str(rule.get("name", ""))
        if scope not in ("agent", "lane") or not name:
            logger.warning("usage_ledger: ignoring budget rule %r", rule)
            continue
        try:
            threshold = float(rule["threshold_usd"])
            window_hours = float(rule.get("window_hours", 24))
        except KeyError, TypeError, ValueError:
            logger.warning("usage_ledger: ignoring budget rule %r", rule)
            continue
        if threshold <= 0 or window_hours <= 0:
            logger.warning("usage_ledger: ignoring budget rule %r", rule)
            continue

        window_s = window_hours * 3600
        bucket = int(now // window_s)
        key = f"{scope}:{name}:{threshold:g}:{window_hours:g}:{bucket}"
        if key in fired_keys:
            continue

        cost = 0.0
        unpriced = 0
        for row in rows:
            if row.get("kind") != KIND_TURN:
                continue
            if str(row.get(scope) or "") != name:
                continue
            if float(row.get("ts", 0)) < bucket * window_s:
                continue
            if row.get("cost_status") == COST_UNPRICED:
                unpriced += 1
            elif row.get("cost_usd") is not None:
                cost += float(row["cost_usd"])

        if cost >= threshold:
            fired_keys.add(key)
            alerts.append(
                {
                    "kind": KIND_BUDGET_ALERT,
                    "ts": round(now, 3),
                    "key": key,
                    "scope": scope,
                    "name": name,
                    "threshold_usd": threshold,
                    "window_hours": window_hours,
                    "window_start": bucket * window_s,
                    "cost_usd": round(cost, 8),
                    "unpriced_turns": unpriced,
                }
            )
    return alerts
