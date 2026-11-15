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
model locked from the row you picked, temperature/effort defaults, and a
16,384-token interactive output reserve. `Ctrl+S` saves and activates. The
value is validated against the model's `max_output` ceiling — a value the
model would 400 on is rejected in the form instead. Clear the field to leave
the provider default untouched. ChatGPT OAuth/Codex shows `backend default`
instead because that endpoint rejects output-token overrides.

Direct activation, no UI:

```bash
/llm fable-slim     # exact name
/llm terra          # unique substring works too
```

A miss lists suggestions instead of guessing.

## Picking a model in the web UI

New Session's Model field and the session panel's Model and Profile field are
one searchable list of every set-up profile × model × effort. Type words in
any order: `codex luna max` finds Codex CLI's gpt-6-luna
at max, `claude code opus max` finds Claude Code's opus at max.

- New Session starts on your default loadout (`kollabor.llm.default_loadout`),
  as the terminal does: its profile, model and effort are selected when the
  form opens. The default is the one of the folder the session will run in
  (the global config, then that folder's project and local config), so choosing
  another folder shows that folder's default, unless you already picked a model
  yourself. A default that names a deleted loadout or profile, or an ACP
  harness, leaves the active profile's own model.
- A pick changes only that session. A native profile takes the model and
  effort as the session's own override (never saved); an ACP harness (Codex
  CLI, Claude Code) owns its model and effort, so they go to its own options,
  as the Model tab sets them. New Session starts on the profile, then applies
  the pick.
- Each opening asks the engine for the list again. Until it answers, the
  picker lists each profile's saved model and says "Loading models…". If the
  request fails, it keeps those rows, says the full model list did not load
  and offers Retry. "No model matches." shows only for a list that loaded.
- Each row names the effort it runs at. A native model also has a default
  row, which runs it at its own default and clears an earlier pick. Its levels
  are ones a request on it carries, by the request's own rule:
  - the registry's (`effort_levels` in `bundles/data/models.json`: Claude
    Opus 4.6 has no xhigh, Claude Haiku 4.5 takes none);
  - for a model the registry says nothing about, its own profile's live
    catalog's (gpt-6-luna stops at max; an empty list is none); another
    endpoint's catalog, or another login's, says nothing about it;
  - else none: no level is guessed. A Claude model the registry doesn't list
    takes none (Sonnet 4.5 takes none).

  A model that doesn't reason, or a provider that sends no effort, gets one
  row with none, and a session on such a provider shows none, whatever was
  saved. Whichever screen set a level, a level the model doesn't take is never
  sent.
- An ACP harness offers the models discovery reported for it, each at the
  levels the harness reported for that model. A model not successfully probed
  has no named effort choices until that model
  reports them; another model's levels, a login's catalog and the registry do
  not establish the harness's capabilities. The agent takes a pick's model
  and level as one change, one change at a time, and a level the harness
  refuses puts its model and level back: a pick never half-applies or mixes
  with one made in another window. A pick names its profile, and the agent
  refuses it once another profile is active, so a switch made in another
  window never gets it. When a pick fails or its answer is lost, the picker
  says what the session now runs and shows it, naming the harness when the
  pick switched to it; so does a switch whose answer is lost. The picker shows
  the session as the engine reads it back, so a newer pick made in another
  window stays. A harness that has reported no model shows its
  profile's name, never the profile's saved model. A model with no
  levels gets one row
  that keeps the harness's level. The engine discovers a configured harness's
  model and effort options in the background, in a separate short-lived
  session: once when it starts, and again behind the list it already has when
  that list is 10 minutes old (on the next ask). The picker answers at once
  from the last good list and never waits for a harness; a harness not learned
  yet shows as loading. A list stays up until a newer one of equal or better
  quality replaces it, however long the engine sat idle; one whose refreshes
  keep failing for 30 minutes gives way to the failure, and a sign-in failure
  replaces it at once. A harness that failed is tried again after 15 s, then
  60 s, then every 300 s, asking only for the models that failed.
  Discovery sends no prompt and does not pass the user's configured MCP servers.
  It can still create an empty session in the provider's history or refresh the
  harness's credentials. If discovery is incomplete, the picker says so and
  offers Retry (`GET /profiles/choices?refresh=true`), which probes the
  incomplete harnesses again at once; model-only choices never claim an
  unobserved effort. A change to a harness's login shows within 10 minutes (or
  after an engine restart); an edit to the profile's launch settings shows as
  soon as the engine has learned it again. `KOLLAB_PICKER_WARM=0` brings back
  the old behavior: the engine discovers when the picker asks, and the picker
  waits for it.
- The engine refreshes any provider catalog older than an hour in the
  background (up to 8 s), so an endpoint's models show without opening
  Settings → Loadouts first; only the first ask after an engine starts waits
  for it, up to 1.5 s.
  A ChatGPT login's list is kept only when the tokens it was fetched with are
  the login signed in as it arrives, and only while that login stays signed
  in: signing in as another account fetches that account's models. A login
  that names no account is known by a digest of its token, and its list stays
  in memory, never in the cache file. Each opening asks the engine again, and
  the engine never answers with another login's catalog; the page shows its
  previous answer only until that one lands.
- An environment variable outranks a pick (`KOLLAB_<PROFILE>_MODEL`,
  `KOLLAB_<PROFILE>_EFFORT`, `KOLLAB_MODEL`, `KOLLAB_EFFORT`): the picker says
  which model kept running instead of showing the pick as applied.
- Saved loadouts are not in the list: using one saves it to its provider
  profile, which every session on that profile shares. Use it from Settings →
  Loadouts.
- The list is `GET /profiles/choices` on the engine
  (`LoadoutManager.list_choices`). The web UI asks for it when the shell loads,
  again when the profiles change and on each opening, and keeps it
  (`model-choices.ts`), so the Model button opens on a complete list; a
  harness still loading is asked about every 1.5 s.
- Settings → Loadouts → Manage Profiles opens the profile editor.

## Launching with a loadout

```bash
kollab --llm fable-slim   # explicit loadout
kollab --llm terra        # implicit (unique match against the catalog)
```

`--llm` resolves in order: existing profile name → loadout (exact, then
unique substring). No match stops the launch with exit status 2 and the
closest names; it never falls back to the default model. At launch the
loadout's fields are applied to its provider
profile **in memory only** — a launch flag never rewrites your saved config.
Activating from inside the app (`/llm`, picker) does persist, matching how
`/model` behaves.

`--model` and `--effort` override individual fields on top of whatever `--llm`
resolved to, and work on their own against the already-active profile:

```bash
kollab --llm fable-slim --effort ultra        # loadout's model, ultra effort
kollab --llm openrouter --model x-ai/grok-4.5 # profile's connection, this model
kollab --model gpt-5.6-terra                  # active profile, this model
```

Precedence is explicit flag → loadout field → provider profile value.

## Fallback when a model can't serve

`/llm fallback <name>` picks the loadout this agent switches to when a turn
fails because its model can't serve it: a usage limit, a quota, a rate limit
that outlasted the retries, or an expired login. The agent says so in one line
in the chat, switches once and runs the turn again, in the same reply. It stays
on the fallback until you switch back with `/llm`. Three cases are not run
again, and the notice asks you to send the message again:

- the fallback is an ACP harness, which has its own conversation;
- the failed turn was an ACP harness's, which has ended before the switch;
- the failed model had already streamed part of its reply, which the new
  answer would join as one.

An ACP harness still running or holding other messages is not switched yet:
switching closes it, which would fail them too. It switches once the last of
them ends, however that one ends. From then until the switch is done, a new
message is refused with a line asking you to send it again. A message from
another agent that the old harness never got, or got and failed, reaches the
new one; a native fallback answers them in one turn. If you pick another
profile before the switch, the switch is dropped, and that profile gets those
messages. If nothing switches (the fallback is out of usage too), they wait
for the agent's next prompt. If activation fails after closing ACP, those
messages stay queued; no native turn starts until a successful switch can read
them from conversation history.

- `/llm fallback` shows the choice; `/llm fallback off` turns it off.
- In the web UI: Settings → Loadouts → Fallback Model, a dropdown of every
  connection (with its own model) and every saved loadout; None turns it off.
- The choice is per agent (`plugins.model_fallback.agents.<agent>`), saved in
  the folder's `.kollab/config.json`. `plugins.model_fallback.loadout` is the
  default for every agent.
- Other errors (a context-window overflow, a bad request) never switch.
- Code: `plugins/model_fallback_plugin.py`, `kollabor/llm/usage_limits.py`.

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
