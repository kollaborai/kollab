# Contract: `/llm` loadouts (as-built)

Status: **reverse-engineered from the implementation**, 2026-08-15
Source: `fbe46e2` (2026-07-30) — shipped with no spec; this document is the
contract that should have preceded it.

Every statement below was read from the code or run. Line references are to the
tree at the time of writing.

> **SUPERSEDED naming:** `--profile` below is the flag name as-built at
> `fbe46e2`. It was later replaced by `--llm`, then by `--provider` (current).
> Read every `--profile` example as `--provider` — the contract's structure
> and IDs are otherwise still accurate.

---

## 1. Concepts

Three layers, one job each.

| Layer | Owns | Where |
|---|---|---|
| **Provider profile** | connection: provider, endpoint, credentials | `kollabor.llm.profiles` |
| **Model** | a catalog entry | `bundles/data/models.json` |
| **Loadout** | provider profile + model + params, named | `kollabor.llm.loadouts` |

A loadout never holds credentials. It points at a profile that does.

## 2. Data model

`Loadout` — `loadout_manager.py:48-72`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | `str` | — | unique key |
| `provider_profile` | `str` | — | must name an existing profile |
| `model` | `str` | — | model id |
| `temperature` | `float \| None` | `None` | `None` = use profile's value |
| `effort` | `str` | `""` | `""` = use profile's value |
| `max_tokens` | `int \| None` | `None` | `None` = use profile's value |
| `description` | `str` | `""` | free text |
| `implicit` | `bool` | `False` | synthesized, never persisted |

**C2.1** `None`/`""` means "inherit from the provider profile". It does not
mean zero and is never written to config.

**C2.2** Only `provider_profile` and `model` are required. `_build_loadout_dict`
(`:228-247`) persists non-default fields only.

**C2.3** Config key is `kollabor.llm.loadouts`. The whole dict is written as one
key, never as a dotted path, so a loadout name containing `.` (e.g. `gpt-5.6`)
stays a literal key instead of being split into nested dicts (`:282-289`).

## 3. Implicit loadouts

**C3.1** For every profile returned by `provider_profiles()`, every model the
bundled registry lists for that provider is automatically a usable loadout.
Nothing is stored.

**C3.2** `provider_profiles()` (`:92-112`) includes a profile iff it has a
provider AND one of: a non-empty API key, `auth_type == "oauth"`, or
(provider in `{custom, local}` AND a configured endpoint). This excludes the
built-in credential-less `default` profile.

**C3.3** The ChatGPT OAuth transport stores provider `openai_responses`; the
registry tags those models `openai`. `_REGISTRY_PROVIDER_ALIASES` (`:45`) maps
between them. Without it an OAuth-only install synthesizes zero loadouts.

**C3.4** Name collisions: an explicit loadout shadows an implicit one of the
same name; among implicits, the first provider profile wins (`:146-174`).

**C3.5 — KNOWN GAP.** Implicit loadouts come from `list_models_for_provider()`
and nothing else. A provider with zero bundled entries contributes zero rows.
`list_models_for_provider('openrouter')` returns `0`, so OpenRouter is
unreachable from `/llm` entirely. See `llm-loadout-live-catalog.md`.

**C3.6 — KNOWN GAP.** `_build_sections()` (`loadout_altview.py:395-396`) does
`if not rows: continue`. A configured provider serving zero models renders
identically to a provider that is not configured at all. The failure is silent.

## 4. Resolution

`resolve(query) -> (Loadout | None, suggestions)` — `:184-221`

**C4.1** Order, first match wins:
1. exact match, explicit
2. exact match, implicit
3. case-insensitive exact
4. unique case-insensitive substring

**C4.2** On no unique match: `(None, suggestions)` where suggestions are up to 8
names containing the query, or the closest names by edit distance
(`get_close_matches`, cutoff `0.4`) when none contain it.

**C4.3** Empty query returns `(None, [])`.

**C4.4** `resolve()` is synchronous and reads only `list_loadouts()`. It
therefore inherits C3.5 — no OpenRouter model is resolvable by name.

## 5. Activation

`async activate(loadout_or_name, event_bus) -> Loadout | None` — `:350-427`

**C5.1** Applies to the loadout's provider profile via
`update_profile(..., save_to_config=True)`: `model` always; `temperature`,
`effort`, `max_tokens` only when the loadout sets them.

**C5.2** Then activates that provider profile:
- if `event_bus` exposes `state_service`:
  `await state_service.set_active_profile(provider_profile, reload_profile=True)`
- otherwise: `profile_manager.set_active_profile()` +
  `api_service.reinitialize_provider(profile)` + `llm_service._load_native_tools()`

**C5.3** Activating a loadout therefore switches **provider and model together**.
This is the provider-switch path; there is no separate one.

**C5.4** Returns `None` without touching the profile if the name does not
resolve or the profile update fails.

**C5.5** Activation from inside the app **persists**. Launch flags do not — see
C7.2.

## 6. Write operations

**C6.1** `create()` (`:248-289`) fails on: empty name, a name that already has an
explicit loadout, an unknown `provider_profile`, or no config available.

**C6.2** `update()` (`:290-331`) refuses implicit and unknown names, and rejects
unknown fields.

**C6.3** `delete()` (`:332-349`) refuses implicit and unknown names.

**C6.4** Implicit loadouts are immutable. They come from the catalog; editing one
branches a new explicit loadout instead.

## 7. Command surface

### 7.1 Slash commands — `handlers/loadout.py:72-97`

| Invocation | Behavior |
|---|---|
| `/llm` | push `LoadoutListAltView` |
| `/llm new` | push `LoadoutFormAltView`, seeded from the active profile |
| `/llm <name>` | resolve + activate, no UI; suggestions on a miss |

Aliases: `/loadout`, `/ld`. Category `SYSTEM`, mode `INSTANT`.

**C7.1.1** List and form are always pushed as separate sequential top-level
AltViews, never nested. Esc from the form reopens a fresh list. Round-trips are
capped at 25 (`_MAX_ROUNDTRIPS`) as a runaway backstop.

**C7.1.2** Both AltViews register `category="internal"` so the altview
integrator does not expose them as palette commands.

### 7.2 Launch flag — `cli.py:422`, `application.py:290-330`

**C7.2.1** `--profile <name>` resolves: existing profile name first, then
loadout (exact, then unique substring).

**C7.2.2** A launch flag applies the loadout's fields to its provider profile
**in memory only** (`persist=False`). It never rewrites saved config.

**C7.2.3** `--default` requires `--profile` (`cli.py:640`).

**C7.2.4** `--profile` is in `daemon_launch_flags` (`cli.py:1897`) — it is
forwarded to the daemon on launch.

**C7.2.5 — GAP.** There are no `--model` or `--effort` flags. Model and effort
can only be set inside the app, or by naming a loadout that carries them.

## 8. Display screens

### 8.0 Screen: `/llm` list — `LoadoutListAltView`

Captured live. Two profiles configured (`openai-oauth`, `openrouter`); note
that OpenRouter has no section — that is gap C3.5/C3.6, not an unconfigured
provider.

```
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
  Loadouts
  type to filter  |  ↑↓ navigate

  Loadouts
    No saved loadouts — press N to create one from any model.
  Models — OpenAI (ChatGPT)
      gpt-5.6                                gpt-5.6 • 1.05M ctx • $5/$30
      gpt-5.6-sol                        gpt-5.6-sol • 1.05M ctx • $5/$30
      gpt-5.6-terra                  gpt-5.6-terra • 1.05M ctx • $2.5/$15
      gpt-5.6-luna                       gpt-5.6-luna • 1.05M ctx • $1/$6
      gpt-5.5                                gpt-5.5 • 1.05M ctx • $5/$30
      gpt-5.4                              gpt-5.4 • 1.05M ctx • $2.5/$15
      gpt-5.4-pro                      gpt-5.4-pro • 1.05M ctx • $30/$180
      gpt-5.4-mini                   gpt-5.4-mini • 400K ctx • $0.75/$4.5
      gpt-5.4-nano                   gpt-5.4-nano • 400K ctx • $0.2/$1.25




▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
 up/down navigate | enter activate | n new | e edit | d delete | esc close
```

**Layout** — `render_frame():345-358`

| Row | Content |
|---|---|
| 0 | `▄` × width, `theme.primary[0]` |
| 1 | `  Loadouts  ` on `primary[0]` / `text_dark` |
| 2 | status line (see 8.0.1) |
| 4… | section headers + rows, windowed on `height - 7` |
| `height-2` | `▄` × width, `theme.dark[1]` |
| `height-1` | footer hint (see 8.0.2) |

**Row format** — `_render_item_row():719-740`

```
  [*] <name>  …right-aligned…  <model> • <ctx> • <$in/$out> [• temp X • effort Y • max N]
  ^^^ ^
  |   name
  marker: "[*]" when active, "   " otherwise
```

Note text is `_loadout_note():162-178`. Override suffixes (`temp`, `effort`,
`max`) appear on **explicit** loadouts only. Implicit rows render in
`text_dim`; explicit in `text`; the selected row inverts to `primary[0]` /
`text_dark`.

**8.0.1 Status line states** — `_render_status():631-657`, first match wins

| Condition | Text | Color |
|---|---|---|
| activating | `  ⟳ activating...` (spinner `\| / - \`, 0.12s) | `text` |
| activated | `  ✓ loadout active: <name> — press any key` | `success` |
| error | `  ! <error>` | `error` |
| note set | `  <note>` | `text_dim` |
| filter active | `  filter: <query>█  (Esc clears)` | `text` |
| default | `  type to filter  \|  ↑↓ navigate` | `text_dim` |

**8.0.2 Footer states** — `_render_footer():741-757`

| Condition | Hint |
|---|---|
| delete armed | ` d confirm delete \| any other key cancels` |
| filter active | ` up/down navigate \| enter activate \| esc clear filter` |
| default | ` up/down navigate \| enter activate \| n new \| e edit \| d delete \| esc close` |

**8.0.3** The `No saved loadouts — press N to create one from any model.` line
renders only for section index 0 (`truly_empty`, `_apply_filter():424`). Every
other empty section is dropped entirely — gap C3.6.

### 8.1 Screen: `/llm new` / `/llm` → `E` — `LoadoutFormAltView`

Reconstructed from the widget definitions at `_build_widgets():875-1005` and
`_render_footer():1282-1301` — not captured live.

```
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
  New Loadout
                                        ← "  Edit Loadout  " in edit mode

    Provider           openai-oauth  (locked)
    Model              gpt-5.6-luna  (locked)
    Name               [ gpt-5.6-luna-custom            ]
    Temperature        ◂──────●─────▸  0.7      (0.0 – 1.0)
    Effort             ▾ default        (default/low/medium/high/xhigh/max/ultra)
    Max Output Tokens  [ 16384                          ]
    Description        [                                ]




▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
 tab/↑↓ field | ←→ adjust | enter/space toggle | ctrl+s save | esc back
```

| Field | Widget | Notes |
|---|---|---|
| Provider | `LabelWidget` | locked, from the row you came from |
| Model | `LabelWidget` | locked; `(none)` when unset |
| Name | `TextInputWidget`, or `LabelWidget` in edit mode | explicit loadouts cannot be renamed — `update()` has no `name` field (C6.2) |
| Temperature | `SliderWidget` | `0.0`–`1.0`, default `0.7` |
| Effort | `DropdownWidget` | `_EFFORT_OPTIONS:66` — `default` maps to `""` (inherit, C2.1) |
| Max Output Tokens | `TextInputWidget`, or `LabelWidget` `backend default` | default `min(registry_default, 16384)`; validated against the model's `max_output` (C8.4) |
| Description | `TextInputWidget` | free text |

Stages — `render_frame():855-872`: `fields` → `saving` (spinner) → `done`
(footer becomes ` any key to continue`).

**8.1.1** Name suggestion (`_suggest_name():193-205`): bare model id, or
`<model>-custom` when branching off an implicit row, with `-2`, `-3`… on
collision. Branching off an implicit row discards that row's own name from the
taken-set first, so the bare id stays available.

### 8.2 Screen: `/model` picker — `ModelPickerAltView` (for comparison)

Same install, same moment, OpenRouter active. 413 rows.

```
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
  SELECT MODEL  --  OpenRouter
  filter: _
  (type a model id, Enter to use it; ↑↓ to pick from the list)
  ▶ * nvidia/nemotron-3-ultra-550b-a55b:free                          current
      ai21/jamba-large-1.7                             256,000 ctx • tools
      aion-labs/aion-2.0                               131,072 ctx • tools
      aion-labs/aion-3.0-mini                          131,072 ctx • tools
      aion-labs/aion-rp-llama-3.1-8b                 32,768 ctx • no tools
      allenai/olmo-3-32b-think                       65,536 ctx • no tools
      amazon/nova-2-lite-v1                          1,000,000 ctx • tools
      anthropic/claude-fable-5                       1,000,000 ctx • tools
      anthropic/claude-opus-4.5                        200,000 ctx • tools
  (1-22 of 413)
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
 Up/Down: Navigate | Enter: Select | Type: filter/enter id | Esc: Cancel
```

**8.2.1** This screen is the proof of gap C3.5: identical install, identical
credentials, 413 rows here and 0 in `/llm`. The difference is
`_fetch_catalog()` at `model_picker_altview.py:127`, which has no counterpart
in `loadout_altview.py`.

**8.2.2** Three things `/model` has that `/llm` lacks and the fix should adopt:
a row counter (`(1-22 of 413)`), free-form entry (type an id not in the list
and press Enter), and an async loading state.

## 9. Picker keys — `loadout_altview.py`

| Key | Action |
|---|---|
| type | filter by name or model |
| `Esc` | clear active filter first; close on second press |
| `Enter` | activate highlighted row |
| `N` | new loadout, pre-filled from highlighted row |
| `E` | edit explicit; on an implicit row, branch a new one (`<model>-custom`) |
| `D` | delete explicit, press twice to confirm |

**C8.1** `n`/`e`/`d` act as commands only while the filter is empty. With a
filter active they are literal characters.

**C8.2** The active row is marked when profile name AND model both match the
active profile (`:444-461`).

**C8.3** Rows scroll on a window of `height - top - 3` (`:660-671`).

**C8.4** The form validates `max_tokens` against the model's `max_output` and
rejects values the provider would 400 on. ChatGPT OAuth/Codex shows
`backend default` because that endpoint rejects output-token overrides.

## 10. Invariants

- **I1** A loadout never stores credentials.
- **I2** Implicit loadouts are never written to config.
- **I3** `create`/`update`/`delete` touch explicit loadouts only.
- **I4** Activation is atomic in intent: profile update must succeed before the
  profile is activated (C5.4).
- **I5** A launch flag never mutates saved config (C7.2.2).
- **I6** Catalog/registry lookups must never raise into the picker —
  `list_provider_models` returns `[]` on any failure.

## 11. Verification as shipped

- `tests/unit/test_loadout_manager.py` — ~30 tests: synthesis, dedup, shadowing,
  resolution order, create/update/delete guards, persist round-trip, activate
- `tests/unit/test_loadout_altview.py` — 3 tests: OAuth profile detection, form
  budget field behavior
- `tests/tmux/specs/loadout_list.json`, `loadout_activate.json`

**C10.1 — GAP.** Both tmux specs exercise the OpenAI profile. Neither can catch
C3.5/C3.6, because a zero-row provider is skipped silently. The feature shipped
green with the hole open.

## 12. Open gaps

| ID | Gap | Tracked in |
|---|---|---|
| C3.5 | Implicit loadouts are registry-only; OpenRouter unreachable | `llm-loadout-live-catalog.md` |
| C3.6 | Zero-row provider renders as absent, silently | same |
| C4.4 | Name resolution inherits the registry-only limit | same |
| C7.2.5 | No `--model` / `--effort` launch flags | same, §CLI |
