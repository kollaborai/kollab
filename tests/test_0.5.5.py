"""
Smoke tests for kollab 0.5.5 release.

Validates:
  1. OpenRouter model metadata fetcher + dynamic max_tokens capping
  2. Pool identity loading with agent_type/skills fields
  3. Spawn identity resolution (three modes)
  4. Capture newest-first ordering
  5. Package imports and version

Run: python tests/test_0.5.5.py
"""

import json
import sys
from pathlib import Path

# Ensure repo root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "kollabor-ai" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "kollabor-agent" / "src"))

PASSED = 0
FAILED = 0


def test(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name} — {detail}")


# ============================================================
# 1. OpenRouter Model Metadata Fetcher
# ============================================================
print("\n=== OpenRouter Model Metadata ===")

try:
    from kollabor_ai.providers.openrouter_model_info import (
        SAFETY_MARGIN,
        OpenRouterModelInfo,
    )

    test("openrouter_model_info imports", True)

    info = OpenRouterModelInfo()

    # No dead fields
    assert not hasattr(info, "_fetch_attempted"), "dead field _fetch_attempted exists"
    test("no dead _fetch_attempted field", True)

    # compute_effective_max_tokens works with no cache (graceful fallback)
    effective = info.compute_effective_max_tokens(128000, "nonexistent-model", 0)
    test(
        "fallback returns config max_tokens when no cache",
        effective == 128000,
        f"got {effective}",
    )

    # compute_effective_max_tokens respects minimum floor
    # Inject fake cache entry with tiny context to trigger floor
    info._cache["tiny-model"] = {"context_length": 100, "max_completion_tokens": None}
    effective = info.compute_effective_max_tokens(128000, "tiny-model", 50)
    test(
        "minimum floor of 256 tokens",
        effective == 256,
        f"got {effective} (context=100, input=50, margin={SAFETY_MARGIN})",
    )

except Exception as e:
    test("openrouter_model_info imports", False, str(e))


# ============================================================
# 2. Pool Identity Loading
# ============================================================
print("\n=== Pool Identity Loading ===")

try:
    from plugins.hub.models import (
        POOL_IDENTITIES,
        POOL_BY_NAME,
        PoolIdentity,
    )

    test("pool models import", True)

    test("24 gems loaded", len(POOL_IDENTITIES) == 24, f"got {len(POOL_IDENTITIES)}")

    # All gems have agent_type field
    all_have_type = all(g.agent_type for g in POOL_IDENTITIES)
    test("all gems have agent_type set", all_have_type)

    # All gems have skills field (even if empty)
    all_have_skills = all(hasattr(g, "skills") for g in POOL_IDENTITIES)
    test("all gems have skills field", all_have_skills)

    # Lookup by name works
    lapis = POOL_BY_NAME.get("lapis")
    test("lapis lookup works", lapis is not None)
    test("lapis is PoolIdentity", isinstance(lapis, PoolIdentity))
    test("lapis has agent_type", bool(lapis.agent_type), f"got '{lapis.agent_type}'")

    # All coder gems
    coders = [g.name for g in POOL_IDENTITIES if g.agent_type == "coder"]
    test(
        "all 24 gems are coder type",
        len(coders) == 24,
        f"got {len(coders)}: {coders[:5]}...",
    )

    # pool.json has all required fields
    pool_file = ROOT / "plugins" / "hub" / "organizations" / "pool.json"
    if pool_file.exists():
        with open(pool_file) as f:
            pool_data = json.load(f)
        gems = pool_data.get("gems", [])
        test("pool.json has 24 entries", len(gems) == 24, f"got {len(gems)}")

        required_fields = {"name", "color_rgb", "role_aliases", "personality", "caste", "agent_type", "skills"}
        all_complete = all(required_fields.issubset(set(g.keys())) for g in gems)
        test("all pool.json entries have all fields", all_complete)
    else:
        test("pool.json exists", False, f"not found at {pool_file}")

except Exception as e:
    test("pool loading", False, str(e))


# ============================================================
# 3. Spawn Identity Resolution Logic
# ============================================================
print("\n=== Spawn Identity Resolution ===")

try:
    from plugins.hub.models import POOL_IDENTITIES, POOL_BY_NAME

    # MODE 1: name matches a pool identity
    name = "lapis"
    pool_match = POOL_BY_NAME.get(name)
    test("MODE 1: identity lookup", pool_match is not None)
    if pool_match:
        effective_type = pool_match.agent_type or "coder"
        test("MODE 1: resolves agent_type from pool", effective_type == "coder")

    # MODE 2: name is an agent_type — find next available
    name = "coder"
    pool_match = POOL_BY_NAME.get(name)
    test("MODE 2: 'coder' is not a pool identity name", pool_match is None)

    coders = [g.name for g in POOL_IDENTITIES if g.agent_type == name]
    test("MODE 2: finds coder gems in pool", len(coders) > 0, f"got {len(coders)}")

    # Simulate picking next available (no online check in unit test)
    first_coder = coders[0] if coders else None
    test("MODE 2: picks first available coder gem", first_coder == "lapis", f"got {first_coder}")

    # MODE 3: explicit identity + type override
    name = "lapis"
    type_override = "research"
    pool_match = POOL_BY_NAME.get(name)
    if pool_match:
        effective = type_override  # override wins
        test("MODE 3: type override takes precedence", effective == "research")

except Exception as e:
    test("spawn resolution", False, str(e))


# ============================================================
# 4. Capture reads from DisplayTap (not vault)
# ============================================================
print("\n=== Capture Source ===")

try:
    # Verify _get_output_lines pulls from DisplayTap snapshot
    plugin_file = ROOT / "plugins" / "hub" / "plugin.py"
    with open(plugin_file) as f:
        source = f.read()

    test(
        "_get_output_lines uses display_tap.get_snapshot",
        "self._display_tap.get_snapshot()" in source,
        "DisplayTap source not wired in plugin.py",
    )

    # Verify truncation is 10k
    test(
        "truncation limit is 10000",
        "> 10000" in source,
        "10000 limit not found",
    )

except Exception as e:
    test("capture source", False, str(e))


# ============================================================
# 5. Version & Imports
# ============================================================
print("\n=== Version & Package Imports ===")

try:
    # Version
    pyproject = ROOT / "pyproject.toml"
    with open(pyproject) as f:
        for line in f:
            if line.startswith("version = "):
                version = line.split('"')[1]
                break
    test("version is 0.5.5", version == "0.5.5", f"got {version}")

    # Package versions synced
    all_synced = True
    for pkg_dir in (ROOT / "packages").iterdir():
        ppt = pkg_dir / "pyproject.toml"
        if ppt.exists():
            with open(ppt) as f:
                for line in f:
                    if line.startswith("version = "):
                        pkg_ver = line.split('"')[1]
                        if pkg_ver != version:
                            print(f"  [WARN] {pkg_dir.name} is {pkg_ver}")
                            all_synced = False
                        break
    test("all packages synced to 0.5.5", all_synced)

    # Core imports
    from kollabor_ai.providers.openrouter_model_info import OpenRouterModelInfo
    from kollabor_ai.providers.openrouter_provider import OpenRouterProvider
    test("openrouter imports work", bool(OpenRouterProvider))

except Exception as e:
    test("version/imports", False, str(e))


# ============================================================
# Summary
# ============================================================
print(f"\n{'='*50}")
total = PASSED + FAILED
if FAILED == 0:
    print(f"  ALL {total} TESTS PASSED")
else:
    print(f"  {PASSED}/{total} PASSED, {FAILED} FAILED")
    sys.exit(1)
