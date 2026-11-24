# Setup Wizard (`/setup`)

A guided, fullscreen wizard that walks a new user through configuring their
first LLM provider — no environment variables or hand-edited JSON required.
Settings > Setup in the Web UI and desktop app also supports ChatGPT sign-in.

```
/setup            # aliases: /onboard, /wizard
```

## First launch

With no usable provider (no profile, and no provider API key in the
environment) the wizard opens by itself on the first interactive launch. It is
the same screen `/setup` opens. Leaving it (Esc) saves
`application.setup_completed`, so it does not open again; `/setup` always does.
A detached daemon or a pipe run has no terminal, so it leaves the wizard for the
next interactive launch.

## Web UI welcome guide

In the Web UI the welcome guide is three cards: Welcome, Join a network and Start
your first agent. It has no provider card of its own: Settings > Setup is the one
provider screen. **Get Started** opens it while the active profile has no real
provider (a fresh install runs the built-in `default` profile, whose provider is
`auto`, and that does not count as a choice). Once a provider is chosen,
**Get Started** moves on to the network and agent cards, and a card whose goal is
already done (a live network, a running agent) is skipped.

While the guide is still loading provider facts, **Open provider setup** remains
available as a direct recovery action. It stays available if `/profiles` fails
or never responds; **Get Started** does not open Setup until the provider lookup
finishes.

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

- **OpenAI — sign in with ChatGPT**: uses the existing OAuth device-code flow
  used by `/login openai`. No API key or endpoint is requested.
- **Azure / Advanced**: routed to manual `config.json` editing (see
  [`docs/providers.md`](../providers.md)), since Azure profiles need an
  endpoint + deployment that the guided flow does not capture.

## Web UI sign-in

Choose **OpenAI (sign in with ChatGPT)** in Settings > Setup. The wizard opens
the device-code page, shows a code to enter, and reports waiting, success,
expiry, denial, and network errors in the sign-in step. After sign-in, choose a
model and save the review. Save and Activate stores the OAuth tokens, creates or
updates the `openai-oauth` profile, and makes it active. Cancel, closing Setup,
or selecting another provider stops the pending sign-in.

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
