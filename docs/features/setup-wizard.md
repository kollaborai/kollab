# Setup Wizard (`/setup`)

A guided, fullscreen wizard that walks a new user through configuring their
first LLM provider — no environment variables or hand-edited JSON required.

```
/setup            # aliases: /onboard, /wizard
```

## What it does

1. **Provider** — pick from Anthropic, OpenAI (API key), OpenAI (ChatGPT
   sign-in), Google Gemini, OpenRouter, or Custom / Local. A blurb and a
   "get a key" link are shown for the highlighted provider.
2. **API key** — paste your key (masked echo). Optional for a local server.
3. **Endpoint** — confirm or edit the base URL. It is prefilled with the
   provider's default; press Enter to accept. Required (and validated) for a
   custom endpoint.
4. **Model** — choose from a curated list (sourced from
   `bundles/data/models.json`, filtered to the selected provider) or type a
   custom model id. The provider's recommended model is highlighted.
5. **Review** — a summary of provider, model, endpoint, and masked key.
   Press `t` to run a **live connection test** (a tiny non-streaming call with
   a 25s timeout) before committing, or Enter to save.
6. **Save & activate** — creates a profile, persists it to `config.json`, and
   switches to it as the active profile (reinitializing the provider). The
   success screen shows the final configuration.

## Navigation

| Key | Action |
| --- | --- |
| `↑` / `↓` | Move within a list |
| `Enter` | Select / advance / (on Review) save & activate |
| `t` | (Review) test the connection first |
| `Esc` | Step back one stage; on the provider list, cancel the wizard |

## Special routes

- **OpenAI — sign in with ChatGPT**: delegates to the existing OAuth device-code
  flow (the same one `/login openai` runs). No API key is requested.
- **Azure / Advanced**: routed to manual `config.json` editing (see
  [`docs/providers.md`](../providers.md)), since Azure profiles need an
  endpoint + deployment that the guided flow does not capture.

## Where things live

| Piece | Path |
| --- | --- |
| Wizard UI (AltView) | [`plugins/altview/setup_altview.py`](../../plugins/altview/setup_altview.py) |
| Command handler | [`kollabor/commands/system_commands/handlers/setup.py`](../../kollabor/commands/system_commands/handlers/setup.py) |
| Model suggestions | `kollabor_ai.model_registry` → `bundles/data/models.json` |
| Profile persistence | `ProfileManager.create_profile(..., save_to_config=True)` |
| Activation | State service profile switch (daemon-safe in attach mode), with coordinator fallback |

The wizard writes an ordinary profile; after setup, switch models and
presets on top of it with `/llm` (see [loadouts.md](loadouts.md)).

## Tests

- Render + flow (deterministic, fake renderer):
  `tests/unit/plugins/test_setup_altview.py`
- Command registration + result handling:
  `tests/unit/commands/test_all_command_handlers.py` (`TestSetupCommandHandler`)
- Live UI (tmux): `tests/tmux/specs/setup-wizard.json`
