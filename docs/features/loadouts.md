# Loadouts

A loadout is a named preset: a provider connection, a model, and the params
you want it launched with (temperature, effort, max tokens). It answers the
question `/profile` never answered cleanly — "I want Fable, but with a smaller
output budget" — without asking you to re-enter an API key or a base URL.

Three concepts, one job each:

- **Provider profile** — the connection: endpoint + credentials. Created once
  via `/setup` (or `/login` for OAuth). Owns the API key.
- **Model** — a catalog entry. Bundled registry (`bundles/data/models.json`)
  plus whatever live catalog the provider exposes.
- **Loadout** — provider profile + model + params, under one name. The only
  layer you interact with day to day.

## Implicit loadouts: every model is already one

For every configured provider profile, every registry model that provider
serves is automatically a usable loadout — no setup, nothing stored. With an
OpenAI key configured, `gpt-5.6-terra` is already a loadout name. The ChatGPT
OAuth transport (`openai_responses`) is aliased to the `openai` catalog, so an
OAuth-only install still gets the full implicit list.

You only *create* a loadout to override something:

```json
// config.json -> kollabor.llm.loadouts
{
  "fable-slim": {
    "provider_profile": "anthropic",
    "model": "claude-fable-5",
    "temperature": 0.3,
    "effort": "high",
    "max_tokens": 32000,
    "description": "Fable with a small output budget"
  }
}
```

Only `provider_profile` and `model` are required. An explicit loadout may
shadow an implicit name (e.g. saving your own `gpt-5.6`) — the explicit one
wins.

## The `/llm` command

`/llm` (aliases `/loadout`, `/ld`) opens a fullscreen picker: saved loadouts first, then
the implicit models grouped per provider, with context window and pricing
notes from the registry. The active one is marked `[*]`.

Picker keys:

- Type to filter (all letters — `n`/`e`/`d` act as commands only while the
  filter is empty). `Esc` clears an active filter first, closes second.
- `Enter` — activate the highlighted loadout.
- `N` — new loadout, pre-filled from the highlighted row.
- `E` — edit an explicit loadout; on an implicit row it branches a new one
  (suggested name `<model>-custom`).
- `D` — delete an explicit loadout (press twice to confirm). Implicit rows
  can't be deleted; they come from the catalog.

The create/edit form arrives fully pre-filled: name suggested, provider and
model locked from the row you picked, temperature/effort defaults, max tokens
seeded from the model's `default_output`. `Ctrl+S` saves and activates. Max
tokens is validated against the model's `max_output` — a value the model would
400 on is rejected in the form instead.

Direct activation, no UI:

```bash
/llm fable-slim     # exact name
/llm terra          # unique substring works too
```

A miss lists suggestions instead of guessing.

## Launching with a loadout

```bash
kollab --profile fable-slim   # explicit loadout
kollab --profile terra        # implicit (unique match against the catalog)
```

`--profile` resolves in order: existing profile name → loadout (exact, then
unique substring). At launch the loadout's fields are applied to its provider
profile **in memory only** — a launch flag never rewrites your saved config.
Activating from inside the app (`/llm`, picker) does persist, matching how
`/model` behaves.

## Relation to `/setup`

`/setup` owns provider connections (keys, endpoints, OAuth); `/llm` owns
everything after that. The old `/profile` command is gone — its list/create/
edit modals are fully replaced by the loadout picker and form. Profiles still
exist under the hood (`kollabor.llm.profiles` holds the connections), and
Azure or fully-custom endpoints are added there by hand — see
`docs/providers.md`.

## Verification

- Unit: `tests/unit/test_loadout_manager.py` (resolution, synthesis, persist,
  activate), `tests/unit/tui/test_loadout_list_filter_keys.py` (filter vs
  command keys).
- Live tmux: `tests/tmux/specs/loadout_list.json` (picker open/close),
  `tests/tmux/specs/loadout_activate.json` (real flow: sections, filter,
  activate, chat confirmation).

Key files: `packages/kollabor-ai/src/kollabor_ai/loadout_manager.py`,
`kollabor/commands/system_commands/handlers/loadout.py`,
`plugins/altview/loadout_altview.py`.
