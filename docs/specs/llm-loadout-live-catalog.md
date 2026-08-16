# Spec: `/llm` live provider catalog

Status: **ready to implement** — all decisions closed (see [Decisions](#decisions))
Date: 2026-08-15

## Problem

`/llm` cannot switch to OpenRouter. The picker renders only the OpenAI group,
so there is no OpenRouter row to press Enter on. `/model` shows 413 OpenRouter
models in the same install.

## Evidence

Every claim below was run or read, not inferred.

| Check | Result |
|---|---|
| `list_models_for_provider('openrouter')` | `0` |
| `list_models_for_provider('openai')` | `9` |
| `list_provider_models(openai-oauth)` live | `8` |
| `/model` picker, OpenRouter | `413` (screenshot) |
| `~/.kollab/config.json` openrouter `api_key` | `secret:keyring:openrouter…` |
| `~/.kollab/config.json` openai-oauth | `auth_type: oauth` |

Both profiles therefore pass the `provider_profiles()` credential filter
(`loadout_manager.py:100-112`) when the OS keyring is reachable.

`bundles/data/models.json` has no OpenRouter entries at all. OpenRouter is a
proxy — its catalog is live-only.

## Root cause

One missing call, in one file.

```
loadout_altview.py:366  _build_sections()
                 :372     list_loadouts()          ← bundled registry ONLY
                 :394     rows = [implicit for this profile]
                 :395-396 if not rows: continue    ← OpenRouter section dropped

model_picker_altview.py:119  spawn_background_task(self._fetch_catalog())
                       :132    list_provider_models(self._profile)
                       :134    self._all_models = self._dedup(... + catalog)
                       :135    self._apply_filter()
```

`loadout_altview.py` contains no `_fetch_catalog` and never imports
`model_catalog`. `/llm` is synchronous registry data; `/model` merges the live
catalog in a background task.

## What already works — do not rebuild

- **Provider switching.** `LoadoutManager.activate()` (`loadout_manager.py:404-421`)
  calls `state_service.set_active_profile(provider_profile, reload_profile=True)`,
  falling back to `profile_manager.set_active_profile()` +
  `reinitialize_provider()` + `_load_native_tools()`. Enter on an OpenRouter row
  would switch provider **and** model today, if the row existed.
- **Per-provider grouping.** `_build_sections()` already emits one
  `Models — <provider>` section per provider profile (`:389-397`).
- **Scrolling.** `_render_rows()` windows on `avail` (`:670-671`). 413 rows will
  scroll without new code.
- **The lookup itself.** `list_provider_models(profile)` (`model_catalog.py:37`)
  is already generic per-profile: OAuth/`openai_responses`, `openrouter`,
  `anthropic`, and any OpenAI-compatible `{base_url}/models`. Returns
  `[{"id","note"}]`, never raises, `[]` on failure, 8s timeout.

## Scope

In scope:
1. `/llm` picker lists every model each configured provider actually serves.
2. `/llm <name>` and the launch flags resolve against that same set.
3. Replace `--profile` with `--llm`, and add `--model` / `--effort`.

Out of scope: changing `/model`, changing `activate()`, adding providers,
touching `models.json`.

As-built behavior this spec changes is documented in
[`llm-loadouts-contract.md`](./llm-loadouts-contract.md). Contract IDs are
referenced below.

## CLI surface

### Today

Only `--profile` exists. There is no `--model` and no `--effort` (contract
C7.2.5) — verified against `cli.py`'s full flag list. `--profile` conflates two
things: a provider profile name and a loadout name.

### Proposed

Decompose it. `--llm` picks the connection, `--model` and `--effort` override on
top.

```bash
kollab --llm openai --model gpt-5.6-luna --effort max
```

| Flag | Takes | Resolves as |
|---|---|---|
| `--llm` | provider profile **or** loadout name | profile first, then loadout (exact → unique substring), same order as C7.2.1 |
| `--model` | model id | applied over whatever `--llm` selected |
| `--effort` | one of `low, medium, high, xhigh, max, ultra` | `EFFORT_LEVELS`, `profile_manager.py:37` |

**Rules**

- **R1** `--model` and `--effort` require `--llm`, unless the active profile
  already supplies the connection — in which case they apply to it.
- **R2** Precedence: explicit flag > loadout field > provider profile value.
  `--llm fable-slim --effort max` uses the loadout's model and temperature but
  `max` effort.
- **R3** In-memory only, no config write. Preserves invariant I5 (C7.2.2).
- **R4** `--effort` is validated against `EFFORT_LEVELS` and rejected at parse
  time with the valid list, not silently dropped.
- **R5** `--model` is **not** validated against the catalog — a model id the
  registry has never heard of must still launch, same as `/model`'s free-form
  entry.

### Usage examples

```bash
# Provider profile + explicit model and effort
kollab --llm openai --model gpt-5.6-luna --effort max

# Switch provider for one run, keep that profile's saved model
kollab --llm openrouter

# OpenRouter model by full id (needs the catalog — see D3)
kollab --llm openrouter --model anthropic/claude-opus-4.5

# A saved loadout, used as-is
kollab --llm fable-slim

# A saved loadout with one field overridden
kollab --llm fable-slim --effort ultra

# Override model/effort on the already-active profile
kollab --model gpt-5.6-terra --effort high

# Combined with an agent bundle
kollab --agent koordinator --llm openrouter --model x-ai/grok-4.5

# Non-interactive
echo "summarize this" | kollab -p --llm openai --model gpt-5.6-luna
```

### Migration

`--profile` is **removed**, not aliased. It is referenced in ~20 files; the
rename must cover:

- `cli.py:422` flag definition, `:640` (`--default requires --profile`),
  `:1897` (`daemon_launch_flags` — forwarded to the daemon on launch, C7.2.4)
- `application.py:290-330` resolution path
- `attach_client.py`, `daemon_pool.py`, `plugins/hub/plugin.py`,
  `plugins/agent_orchestrator/orchestrator.py`, `plugins/deep_thought/orchestrator.py`
- `tests/unit/plugins/test_plugin_cli_args.py`, `tests/tmux/phase_4_5_smoke.sh`,
  `tests/tmux/runtime_docker_ui_smoke.sh`
- docs: `configuration.md`, `getting-started.md`, `faq.md`, `features/agents.md`,
  `features/profiles.md`, `features/loadouts.md`, `architecture-overview.md`

See decision **D5** on whether a deprecation shim is worth it.

## Proposed design

Generalize the model picker's pattern from one profile to N.

### 1. Cache layer — `model_catalog.py`

`list_provider_models()` has **no cache**; every call is a live fetch.
`_openrouter_models()` constructs a fresh `OpenRouterModelInfo()` per call, so
its 1-hour TTL cache (`openrouter_model_info.py:21`) is never reused.

Add a module-level TTL cache keyed by profile name:

```
async get_provider_models(profile, *, max_age=3600) -> List[Dict[str,str]]
     cached_provider_models(profile) -> List[Dict[str,str]]   # sync, cache-only
```

`cached_provider_models()` never blocks and never fetches — it is what the sync
callers below read.

### 2. Manager — `loadout_manager.py`

`list_loadouts()` gains catalog-sourced implicit loadouts, read from the cache
only (stays sync, stays non-blocking):

- registry models, as today
- plus `cached_provider_models(profile)` entries not already present
- existing dedup rule unchanged: explicit shadows implicit, first provider
  profile wins on name collision

Add `async refresh_catalogs()` to warm the cache for every
`provider_profiles()` entry, in parallel.

### 3. Picker — `loadout_altview.py`

Mirror `model_picker_altview.py:116-140`:

- `on_enter`: render registry rows instantly (unchanged), then
  `spawn_background_task(self._fetch_catalogs())`
- `_fetch_catalogs()`: `await manager.refresh_catalogs()`, then `_refresh()` +
  `_apply_filter()`
- spinner while loading — the view already has `_spinner` (`:313`)
- drop the `if not rows: continue` skip for a provider whose fetch is pending,
  so an empty OpenRouter group shows "loading…" rather than vanishing

### 4. Name resolution

`resolve()` (`loadout_manager.py:184`) is sync and feeds both `/llm <name>` and
`kollab --llm <name>`. It picks up catalog entries for free via the cache, but
only when the cache is warm. See decision **D3**.

## Target screens

As-built screens and their layout contract are in
[`llm-loadouts-contract.md` §8](./llm-loadouts-contract.md#8-display-screens).
Only the deltas are shown here.

### T1 — `/llm`, catalogs still loading

Opens instantly on registry rows. Providers with a pending fetch show a
spinner row instead of vanishing (fixes C3.6).

```
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
  Loadouts
  ⟳ loading catalogs (1/2)…                      ← NEW: status line state

  Loadouts
    No saved loadouts — press N to create one from any model.
  Models — OpenAI (ChatGPT)
      gpt-5.6                                gpt-5.6 • 1.05M ctx • $5/$30
      gpt-5.6-luna                       gpt-5.6-luna • 1.05M ctx • $1/$6
  Models — OpenRouter                                        ← NEW: shown
    ⟳ fetching catalog…                                      ← NEW: not skipped
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
 up/down navigate | enter activate | n new | e edit | d delete | esc close
```

### T2 — `/llm`, catalogs merged

```
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
  Loadouts
  type to filter  |  ↑↓ navigate                          (422 models)
                                            ↑ NEW: row counter, cf. C8.2.2
  Loadouts
    No saved loadouts — press N to create one from any model.
  Models — OpenAI (ChatGPT)
      gpt-5.6                                gpt-5.6 • 1.05M ctx • $5/$30
      gpt-5.6-luna                       gpt-5.6-luna • 1.05M ctx • $1/$6
  Models — OpenRouter                                        ← 413 rows
  [*] nvidia/nemotron-3-ultra-550b-a55b:free    …:free • 262K ctx • tools
      ai21/jamba-large-1.7                    …-1.7 • 256K ctx • tools
      anthropic/claude-opus-4.5         …opus-4.5 • 200K ctx • tools
      anthropic/claude-fable-5            …fable-5 • 1M ctx • tools
      x-ai/grok-4.5                          …grok-4.5 • 2M ctx • tools
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
 up/down navigate | enter activate | n new | e edit | d delete | esc close
```

Enter on `anthropic/claude-opus-4.5` switches profile **and** model in one
step — `activate()` already does this (C5.3). Nothing new is needed for the
switch itself; the row simply has to exist.

### T3 — `/llm` filtered

The existing filter (`_apply_filter():414-436`, matches name or model) now
spans providers, which is the practical answer to a 413-row group (D2).

```
  Loadouts
  filter: opus█  (Esc clears)                              (3 of 422)

  Models — OpenRouter
      anthropic/claude-opus-4           …opus-4 • 200K ctx • tools
      anthropic/claude-opus-4.1         …opus-4.1 • 200K ctx • tools
      anthropic/claude-opus-4.5         …opus-4.5 • 200K ctx • tools
```

### T4 — catalog fetch failed

Failure must degrade to registry rows, never empty the view (invariant I6).

```
  Loadouts
  ! openrouter catalog unavailable — showing bundled models only

  Models — OpenAI (ChatGPT)
      gpt-5.6                                gpt-5.6 • 1.05M ctx • $5/$30
  Models — OpenRouter
    catalog unavailable — /llm again to retry
```

### Screen deltas

| # | Change | Fixes |
|---|---|---|
| S1 | status line: `⟳ loading catalogs (n/m)…` state | — |
| S2 | keep a section whose fetch is pending or empty; render a placeholder row | C3.6 |
| S3 | row counter on the status line, right-aligned | C8.2.2 |
| S4 | status line: `! <provider> catalog unavailable` state | I6 |
| S5 | filtered counter `(n of m)` | D2 |

S1–S5 are additions to `_render_status()` and `_flat_lines()`. No new keys, no
new footer states, no change to `_render_item_row()`.

## Decisions

Each was settled by existing precedent in this codebase, not by preference.

**D1 — Fetch strategy: eager, all configured providers on open.**
`model_picker_altview.py:119` already fetches eagerly on open for one provider;
this is the same thing for N. Lazy would make the filter silently incomplete —
typing `opus` would search only the providers you happened to scroll past,
which is the same class of bug as C3.6.

**D2 — 413 rows: show all, no cap.**
`_apply_filter():414-436` already matches on name *or* model, so the filter is
the handling. A cap adds a second concept for a view that already scrolls
(`_render_rows():670-671`).

**D3 — Cache: persist to disk, versioned.**
Precedent is `agent_manager.py:55-57` — `CACHE_VERSION = 3`,
`AGENT_CACHE_FILE = get_config_directory() / "agent_metadata.cache"`, with
`_load_cached_agents():416` discarding on version mismatch and
`_save_cached_agents():446` writing JSON. Follow it exactly:
`~/.kollab/model_catalog.cache`, versioned JSON, TTL on top. Survives restarts,
no launch latency, and makes D1 nearly free after first run.

**D4 — Substring ambiguity: accept.**
`resolve():214-218` already returns a suggestion list when a substring is
ambiguous. That is the designed fallback, not a degradation.

**D5 — `--profile`: hard-remove, no alias.**
Directed. A silent alias means two names in the docs and in every orchestrator
that shells out; removal makes the breakage loud at parse time.

**D6 — Bare `--model` / `--effort`: allowed.**
`kollab --model gpt-5.6-terra` applies to the active profile. Requiring a
redundant `--llm` on every invocation is friction with no safety gain.

## Files

| File | Change |
|---|---|
| `packages/kollabor-ai/src/kollabor_ai/model_catalog.py` | TTL cache + sync accessor |
| `packages/kollabor-ai/src/kollabor_ai/loadout_manager.py` | merge cached catalog; `refresh_catalogs()` |
| `plugins/altview/loadout_altview.py` | background fetch, loading state, keep empty groups |
| `kollabor/cli.py` | `--profile` → `--llm`; add `--model`, `--effort`; update `daemon_launch_flags` |
| `kollabor/application.py` | apply `--model` / `--effort` over the resolved connection (R2) |
| callers + docs | see [Migration](#migration) |

Ship in two independently-testable phases: **P1** catalog (the bug), **P2** CLI
flags (the redesign). P2 depends on P1 only for `--model` with an OpenRouter id.

## Test plan

Must stay green: `tests/unit/test_loadout_manager.py` (~30 tests, incl.
`test_implicit_synthesis_*`, `test_resolve_*`), `tests/unit/test_loadout_altview.py`,
`tests/tmux/specs/loadout_list.json`, `tests/tmux/specs/loadout_activate.json`.

New:
- unit: catalog entries become implicit loadouts; explicit still shadows;
  cold cache degrades to registry without raising; fetch failure never empties
  the list
- unit: cache TTL expiry and hit/miss
- unit: a provider with zero rows still yields a section (S2 / C3.6) — this is
  the assertion that would have caught the original bug
- unit: `--effort` rejects a value outside `EFFORT_LEVELS` (R4); `--model`
  accepts an unknown id (R5); precedence flag > loadout > profile (R2)
- tmux spec `loadout_openrouter_catalog.json` — open `/llm`, assert an
  OpenRouter section with `anthropic/` or `openai/` prefixed rows appears.
  Model on `tests/tmux/specs/regression_openrouter_model_catalog.json`, which
  already covers this for `/model`. Pass `--no-daemon`; harness exports
  `KOLLAB_NO_KEYRING=1`.

## Risks

1. **Keyring gate.** With `KOLLAB_NO_KEYRING=1`, `get_api_key()` fails and
   OpenRouter drops out of `provider_profiles()` entirely — verified. Tests must
   inject a profile stub, not rely on real credentials.
2. **Per-frame cost.** `_render_rows()` rebuilds `_flat_lines()` every frame at
   12fps. At 400+ rows this is a per-frame O(n) walk. Measure; memoize on
   `(query, sections)` if it shows.
3. **Fetch on open.** N providers = N HTTP calls. The cache makes this once per
   hour, but a cold open on a slow link shows registry rows first — acceptable,
   and the same tradeoff `/model` already ships.

## Prior corrections

Two claims made earlier in this session were wrong and are corrected here:
"nothing switches the active provider profile" (`activate()` does), and the
framing of the empty registry as a reason the design could not work (it is the
one missing call, nothing more).
