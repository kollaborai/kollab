#!/usr/bin/env python3
"""Regenerate the model directory (bundles/data/models.json) from external
sources, so model capabilities (image input, reasoning, tools, context
windows, pricing) stay current without hand-editing every release.

Sources (both keyless, fetched live by default):
  * models.dev api.json          — cross-provider catalog (openai, anthropic,
                                   google; provider slugs map to kollab's)
  * OpenRouter /api/v1/models   — the full OpenRouter catalog, stored under
                                   its vendor-prefixed ids with provider
                                   "openrouter"

Merge semantics are curated-safe:
  * existing entries are UPDATED only on fields the sources provide;
    curated-only fields (notes, effort_levels, thinking_default, default_output,
    supports_streaming, retired, aliases) are never touched;
  * new models are ADDED, flagged with an "Auto-generated" note for review;
  * nothing is ever deleted — retire models by hand;
  * _meta.updated bumps only when something actually changed.

Modes:
  * default          — fetch, merge, write
  * --check          — fetch, merge, report; exit 1 if the file would change
                       (drift gate for CI / release prep)
  * --source-* PATH  — use a local payload instead of the network (offline
                       review, tests)

Run it during release prep (docs/release-process.md) or let the scheduled
model-directory-refresh workflow open a PR with the diff.
"""

import argparse
import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / "bundles" / "data" / "models.json"
MODELS_DEV_URL = "https://models.dev/api.json"
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"

# models.dev provider slugs this directory carries, mapped to kollab provider
# names. Anything else in the payload is skipped (openrouter models come from
# the OpenRouter source instead). Extend deliberately: every name here feeds
# the model picker and compaction defaults.
MODELS_DEV_PROVIDERS = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "gemini",
}

# Fields a source may refresh on an existing entry. Everything else on the
# entry is curated-only and survives every run.
SOURCED_FIELDS = (
    "context_window",
    "max_output",
    "pricing_in",
    "pricing_out",
    "pricing_cached_in",
    "supports_tools",
    "supports_thinking",
    "supports_vision",
    "supports_sampling",
    "released",
)


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "kollab-model-directory/1.0 (+https://github.com/kollaborai/kollab)",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def unix_to_date(unix: int) -> str:
    return datetime.fromtimestamp(int(unix), tz=timezone.utc).date().isoformat()


def openrouter_catalog_id(model_id: str) -> str:
    """The catalog key for an OpenRouter id: the ":free"/":nitro" style tag
    comes off; lookups match tagged variants through longest-prefix."""
    return model_id.rsplit(":", 1)[0] if ":" in model_id else model_id


def _as_number(value) -> float:
    return float(value) if not isinstance(value, bool) else 0.0


def map_models_dev_model(name: str, entry: dict, provider_slug: str) -> dict:
    """Map one models.dev model entry onto the kollab schema."""
    mapped: dict = {"provider": MODELS_DEV_PROVIDERS[provider_slug]}
    limit = entry.get("limit")
    if not isinstance(limit, dict):
        limit = {}
    if isinstance(limit.get("context"), int) and limit["context"] > 0:
        mapped["context_window"] = limit["context"]
    if isinstance(limit.get("output"), int) and limit["output"] > 0:
        mapped["max_output"] = limit["output"]
    modalities = entry.get("modalities")
    if not isinstance(modalities, dict):
        modalities = {}
    input_modalities = modalities.get("input")
    if not isinstance(input_modalities, list):
        input_modalities = []
    mapped["supports_vision"] = "image" in input_modalities
    mapped["supports_tools"] = bool(entry.get("tool_call"))
    mapped["supports_thinking"] = bool(entry.get("reasoning"))
    temperature = entry.get("temperature")
    if isinstance(temperature, bool):
        mapped["supports_sampling"] = temperature
    if entry.get("release_date"):
        mapped["released"] = entry["release_date"]
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        pricing = {}
    if isinstance(pricing.get("input"), (int, float)):
        mapped["pricing_in"] = round(_as_number(pricing["input"]), 6)
    if isinstance(pricing.get("output"), (int, float)):
        mapped["pricing_out"] = round(_as_number(pricing["output"]), 6)
    if isinstance(pricing.get("cache_read"), (int, float)):
        mapped["pricing_cached_in"] = round(_as_number(pricing["cache_read"]), 6)
    return mapped


def map_openrouter_model(entry: dict) -> dict:
    """Map one OpenRouter catalog entry onto the kollab schema."""
    mapped: dict = {"provider": "openrouter"}
    if isinstance(entry.get("context_length"), int) and entry["context_length"] > 0:
        mapped["context_window"] = entry["context_length"]
    top_provider = entry.get("top_provider")
    if not isinstance(top_provider, dict):
        top_provider = {}
    max_completion = top_provider.get("max_completion_tokens")
    if isinstance(max_completion, int) and max_completion > 0:
        mapped["max_output"] = max_completion
    architecture = entry.get("architecture")
    if not isinstance(architecture, dict):
        architecture = {}
    input_modalities = architecture.get("input_modalities")
    if not isinstance(input_modalities, list):
        input_modalities = []
    mapped["supports_vision"] = "image" in input_modalities
    parameters = entry.get("supported_parameters") or []
    if not isinstance(parameters, list):
        parameters = []
    mapped["supports_tools"] = "tools" in parameters
    mapped["supports_thinking"] = "reasoning" in parameters or "include_reasoning" in parameters
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        pricing = {}
    # OpenRouter prices are per-token strings; the directory is per-million.
    if pricing.get("prompt") not in (None, ""):
        mapped["pricing_in"] = round(_as_number(pricing["prompt"]) * 1_000_000, 4)
    if pricing.get("completion") not in (None, ""):
        mapped["pricing_out"] = round(_as_number(pricing["completion"]) * 1_000_000, 4)
    cache_read = pricing.get("input_cache_read")
    if cache_read not in (None, ""):
        mapped["pricing_cached_in"] = round(_as_number(cache_read) * 1_000_000, 4)
    if isinstance(entry.get("created"), int) and not isinstance(entry["created"], bool):
        mapped["released"] = unix_to_date(entry["created"])
    return mapped


def merge_sources(data: dict, models_dev: dict, openrouter: dict):
    """Plan the merge. Returns (additions, planned) where additions maps new
    catalog keys to mapped entries (curated-safe: unsupported provider slugs
    are skipped here) and planned records every existing-entry field change."""
    models = data.get("models", {})
    additions: dict = {}
    changed: list = []

    mapped_by_key: dict = {}
    if not isinstance(models_dev, dict):
        models_dev = {}
    for provider_slug, provider_data in models_dev.items():
        target = MODELS_DEV_PROVIDERS.get(provider_slug)
        if not target or not isinstance(provider_data, dict):
            continue
        provider_models = provider_data.get("models")
        if not isinstance(provider_models, dict):
            continue
        for name, entry in provider_models.items():
            if isinstance(entry, dict) and not entry.get("deprecated"):
                mapped_by_key[name] = map_models_dev_model(name, entry, provider_slug)
    if not isinstance(openrouter, dict):
        openrouter = {}
    openrouter_entries = openrouter.get("data")
    if not isinstance(openrouter_entries, list):
        openrouter_entries = []
    for entry in openrouter_entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            continue
        key = openrouter_catalog_id(entry["id"])
        if key in mapped_by_key:
            continue  # a curated/native entry owns this model name
        mapped_by_key[key] = map_openrouter_model(entry)

    for key, mapped in sorted(mapped_by_key.items()):
        if not isinstance(mapped.get("context_window"), int) or mapped["context_window"] <= 0:
            # No context window means it is not a chat model (image generators
            # and friends). The registry's required field is context_window,
            # so such entries cannot be represented here; skip them.
            continue
        existing = models.get(key)
        if existing is None:
            additions[key] = mapped
        else:
            for field in SOURCED_FIELDS:
                if field not in mapped:
                    continue
                # Sampling defaults to supported when the catalog is silent.
                # Keep that implicit default (and an unchanged catalog) while
                # still allowing a sourced True to clear an explicit False.
                if field == "supports_sampling" and mapped[field] is True and field not in existing:
                    continue
                if existing.get(field) != mapped[field]:
                    changed.append((key, field, existing.get(field), mapped[field]))
    return additions, {"added": sorted(additions), "changed": changed}


def apply_merge(data: dict, models_dev: dict, openrouter: dict, today: str):
    """Apply a planned merge in place. Returns (changed, report lines)."""
    additions, planned = merge_sources(data, models_dev, openrouter)
    report: list = []
    models = data.setdefault("models", {})
    for key, mapped in additions.items():
        entry = dict(mapped)
        source = "OpenRouter" if entry.get("provider") == "openrouter" else "models.dev"
        entry["notes"] = (
            f"Auto-generated from {source} on {today}; review capability and pricing before relying on them."
        )
        models[key] = entry
        report.append(f"added {key} ({entry.get('provider')})")
    for key, field, old, new_value in planned["changed"]:
        models[key][field] = new_value
        report.append(f"{key}.{field}: {old!r} -> {new_value!r}")
    if not report:
        return False, ["no changes; model directory already matches the sources"]
    data.setdefault("_meta", {})["updated"] = today
    return True, report


def load_registry(path: Path) -> dict:
    return json.loads(path.read_text())


def write_registry(path: Path, data: dict) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what would change; exit 1 if the registry is stale",
    )
    parser.add_argument("--dry-run", action="store_true", help="never write")
    parser.add_argument(
        "--source-models-dev",
        type=Path,
        default=None,
        help="local models.dev api.json payload instead of the network",
    )
    parser.add_argument(
        "--source-openrouter",
        type=Path,
        default=None,
        help="local OpenRouter /api/v1/models payload instead of the network",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=REGISTRY,
        help="registry file (default: bundles/data/models.json)",
    )
    args = parser.parse_args(argv)

    try:
        models_dev = (
            json.loads(args.source_models_dev.read_text()) if args.source_models_dev else fetch_json(MODELS_DEV_URL)
        )
        openrouter = (
            json.loads(args.source_openrouter.read_text()) if args.source_openrouter else fetch_json(OPENROUTER_URL)
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot load sources: {exc}")
        return 2

    data = load_registry(args.registry)
    today = date.today().isoformat()
    changed, report = apply_merge(data, models_dev, openrouter, today)

    for line in report:
        print(line)
    if not changed:
        if args.check:
            print(f"OK: {args.registry} matches the sources")
        return 0

    if args.check:
        print(f"STALE: {args.registry} differs from the sources ({len(report)} change(s))")
        return 1
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    write_registry(args.registry, data)
    print(f"wrote {args.registry} ({data['_meta']['updated']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
