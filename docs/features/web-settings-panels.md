# Web Settings Panels

The web UI (`kollab --web-ui`) has a **Settings** dialog with nine tabs. Most mirror the terminal's fullscreen screens (`/config`, `/llm`, `/model`, `/setup`, `/connect`, `/voice config`) through the same daemon-owned code, so a change saved in the browser shows up in the terminal and the other way round. The Prompts tab is browser-only: it inspects default and agent-bundle sections and saves per-user overrides without changing bundled files. On a phone the dialog is a full-screen sheet.

Design and deviations: [`docs/specs/webui-unified-config.md`](../specs/webui-unified-config.md).

## Tabs

| Tab | What it is | Where a change goes |
| --- | --- | --- |
| Session | This chat's model and profile override. Apply changes the session only. | Nowhere on disk. It lasts as long as the session. |
| Configuration | Every setting, in collapsed sections with a count per section. Checkboxes, sliders, dropdowns and text fields; managed settings are read-only. | The footer saves to **Project** (`.kollab/config.json` in the workspace) or **Globally** (`~/.kollab/config.json`). The two real paths are printed on the buttons. Only values that differ from the defaults are written. |
| Loadouts | The same list as terminal `/llm`: use one, create, edit, delete (delete asks twice). | The profile manager the terminal `/llm` uses. |
| Model | Reasoning effort and the model picker for the active provider (terminal `/model`). | The profile manager the terminal `/model` uses. |
| Setup | The first-run wizard as steps: provider, API key, endpoint, model, optional connection test, Save and Activate. A bad key shows a redacted error; a missing key jumps back to the key step. | A new profile, activated, through the same code as terminal `/setup`. |
| Network | Your agent network: status, this device, join requests with accept and reject, trust level, rename, and New Join Code. Knocks opens the knock screen: ringing knocks (accept, reject, block), the knocks this device placed (stop), missed knocks, blocked routes, contacts, and who may knock. | The network state the terminal `/connect` and `/connect knocks` use. |
| PhoneLink | Link a phone with an expiring QR code and local approval over the existing Kollab connection. Advanced Settings contains optional WireGuard controls. | Private phone identities on this computer; no provider credential sync to the phone. |
| Voice | Classifier, transcript context, speech engine, Kokoro and VibeVoice settings, plus transcription, microphone and current readiness (terminal `/voice config`). | **Save for This Device**, to `~/.kollab/voice/preferences.json` on the device running Kollab. Saving never starts listening. |
| Prompts | Browse the default prompt and agent-bundle sections in assembly order, edit sections marked editable, and preview the assembled prompt. | Per-user overrides under `~/.kollab/prompt-overrides/<bundle>/<section>.md`; bundled prompt files are unchanged. |

A change that is not saved yet is marked "changed" on its field; closing the dialog with unsaved changes asks first.

## Slash commands in the chat

Typing a panel command in the web chat opens its tab instead of sending a message:

| You type | Opens |
| --- | --- |
| `/config` | Settings, Configuration |
| `/llm` (also `/loadout`, `/ld`) | Settings, Loadouts |
| `/model` (also `/mod`, `/m`) | Settings, Model |
| `/setup` (also `/onboard`, `/wizard`) | Settings, Setup |
| `/connect`, `/connect code`, `/connect knocks` | Settings, Network |
| `/voice config` | Settings, Voice |
| `/prompts` | Settings, Prompts |

`/voice`, `/voice on` and `/voice off` control listening through normal chat commands. `/voice status` reports runtime diagnostics.

`/model effort high` and `/llm <name>` with an argument run as typed and answer in the chat. Picking a command from the slash menu opens its tab too. A command that only works in a terminal (`/matrix`, for one) answers "needs the terminal UI" and the turn ends; it never hangs.

The terminal commands continue to open their existing fullscreen screens; `/prompts` is a web-chat panel command. An attached terminal (`kollab` in daemon mode) still opens `/config`.

## Secrets and join codes

- A secret setting (API keys, tokens) is shown as a password field. The server tells the page only that it is set, never its value. Leave it alone to keep it; clear it to remove it.
- A join code is shown once, with a countdown, and has no copy button. It is dropped when you press Done, close the dialog or it expires.
- `/connect` refuses a join code typed as an argument. Join codes are redacted from the engine's log output and from the saved conversation.
- Panel payloads can carry secrets, so they are never logged.

## Read-only network

One process per workspace runs the network. When that is another chat in the same web UI, the engine answers this chat's Network tab from that chat's daemon, Knocks and New Join Code included, so every chat in the folder shows the full tab. Only when a terminal window, or an agent left running by an earlier web UI, runs the network does the tab show the network and device read-only, with one plain line saying so; connect actions then answer 403.

## Config reloads

The daemon polls the config files once a second and reloads when another process saved, so a terminal `/config` Global save reaches a running daemon (and its web UI) within a second or two.

## Testing

- `tests/unit/test_fullscreen_unavailable.py`, `tests/unit/test_altview_unavailable.py`: web commands never open a fullscreen view.
- `tests/tmux/specs/config_attach_daemon_opens.json`: default (daemon plus attached window) launch in a throwaway HOME; the attached `/config` opens and a Global slider save persists in the file. Run with `tests/tmux/lib/test_runner.sh tests/tmux/specs/config_attach_daemon_opens.json`.
