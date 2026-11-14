# Plugins for the web UI and the backend

Issue: #1028  
Status: Approved (2026-11-14, by the owner, with mockups of the main surfaces; mockups of the off, failed,
stopped and empty states are due before build step 3); review rounds in progress, see `plugins.review.md`  
Owner: maintainers

## Problem

Kollab's terminal can be extended with plugins. The web UI and the engine cannot, and nothing can be
installed, pinned or trusted from outside this repository.

Who has the problem: anyone who wants their own screens or background jobs in kollab without changing
kollab, and the maintainer, whose PR-chain runner is the first real case.

- The loader imports Python plugin modules from the bundled `plugins/` folder, `~/.kollab/plugins` and
  `<cwd>/.kollab/plugins` (`packages/kollabor-plugins/src/kollabor_plugins/registry.py:49-70`). There is
  no package format, install command, pin or trust prompt, and the project folder is imported without
  asking (#1035).
- The web UI is one prebuilt bundle (`packages/kollabor-webui/frontend/dist`). Its main-area views are a
  closed list (`SessionView`, `components/shell/SessionPanel.tsx:87`), the right-hand menu rows are
  hard-coded (MCP Servers, Who Is Online, Settings, Clear History, `SessionPanel.tsx:476`) and daemon
  panels are a closed tuple (`PANEL_MODULES`, `kollabor/panels/base.py:45`).
- The engine (`kollabor_engine`, port 7433) mounts a fixed list of routers (`server.py:344-383`).
- The configuration menu finds plugin settings with its own scanner, apart from the loader: it walks
  the bundled `plugins/` folder by file name (`*_plugin.py`, `<folder>/plugin.py`), falls back to a
  `pkgutil` scan of the installed `plugins` package, imports every module and guesses the class from a
  name ending in `Plugin` (`packages/kollabor-tui/src/kollabor_tui/config_widgets.py:137-386`).
  `PluginConfigManager` (`kollabor_config/plugin_config_manager.py`) keeps a third view. A plugin outside
  the bundled folder never shows in `/config`, in the terminal or in the web's Settings → Configuration.

So every new web feature is a core change. The PR-chain runner shows that cost. It is a maintainer's
tool that takes one PR at a time through review, fix, audit, UI review, CI and merge, each stage run by
a fresh agent. Outside kollab it rebuilt what kollab already has: its own web board on its own port, a
loop kept alive with `nohup`, an agent launcher made of command lines, its own spend math and its own
config page. Inside kollab it got a hard-coded read-only reader and dialog (#1008:
`kollabor_engine/pr_chain.py`, `routes/pr_chain.py`, `components/pr-chain/`).

## Goal and non-goals

Goal: one plugin package format that can add anything to kollab, in the web UI and the backend,
installed from a git repository and pinned to a commit.

The acceptance test is the PR-chain runner rebuilt as a plugin at full parity with its current board:
the Board, Pull Requests, Issues, Spend and Config pages, the loop, the stage agents and the spend
numbers, installed into a throwaway HOME from its git URL.

A plugin can add:

1. Web UI: a row in the right-hand menu, a main-area tab beside Chat and Trajectory, a modal with tabs,
   a full page with tabs, a Settings section, composer slash commands, chat cards and notices.
2. Backend: engine routes, supervised background processes and agent runs through the user's loadouts.
3. The pieces kollab already reads from files (agents, skills, slash commands, config hooks, MCP
   servers) and a `BasePlugin` that loads in every agent, terminal and web alike.

Every surface that lists plugins or their settings reads one discovered list, so nothing about a
plugin is hard-coded in kollab: the terminal's `/config` and the web's Settings → Plugins show the same
plugins, built-in and installed, with the same fields.

Non-goals:

- A sandbox for plugin code (decision D1).
- A hosted registry, a marketplace or an Apps catalog entry.
- New project-folder plugins. Today's import of `<cwd>/.kollab/plugins` is bug #1035, fixed apart from
  this spec.
- Hot module reload. A page reload picks up a rebuilt plugin.
- Publishing an SDK package to npm. The types ship inside the hello plugin template.
- Moving the bundled `plugins/` (hub, voice, MCP and the rest) into packages.
- Changing what pr-chain does. It keeps its stages, guards and files.

## What people see

### Web UI

Two words keep one meaning everywhere: a **chain** is one PR moving through the stages, and an **agent
run** is one agent process.

**Right-hand menu.** Each enabled plugin can add rows after Who Is Online, before Settings: an icon, a
Title Case label and a short count on the right, like MCP Servers' `2/5`. PR Chain shows `PR Chain  4`,
the chains running now. Hovering shows `4 chains running, 1 chain needs you`; the second part shows
only when something needs you, and both parts use the singular for 1. When kollab itself knows the
plugin is unwell, kollab sets the count: `Stopped` after the crash limit, `Failed to Load` when its code
failed. Plugin rows sort by plugin name. The row text itself reads `4 · 1 needs you` whenever something needs you, on every device. On a
touch screen (`pointer: coarse`) every plugin row, tab and button is at least 44 px tall; the plugin host
sets that size for everything inside `[data-plugin]`. On a phone the menu is the existing right-hand sheet and the rows are there too.

**What a row opens.** The plugin picks one of three per row, or an external link:

- **Tab**: a main-area view beside Chat, Trajectory, Code Editor, Git and Terminal. It belongs to the
  open chat and receives its session and folder. The view list at the top of the right-hand menu shows
  it with the plugin's icon. A tab that needs a folder hides for a chat with none, like Code Editor. Each chat remembers its own tab;
  a chat that does not offer the plugin's tab shows Chat.
- **Modal**: a dialog with the plugin's tabs, the same shape as Settings. On a phone it fills the screen.
- **Page**: the whole main area with the plugin's own tab bar. PR Chain opens a page with Board, Pull
  Requests, Issues, Spend and Config. Picking an agent in the left sidebar leaves the page.

On a phone a page or modal fills the screen and its tab bar scrolls sideways.

**Plugin agents.** An agent a plugin starts is a normal kollab session. While it runs it shows in the
left sidebar under a group named after the plugin (`PR Chain  2 Agent Runs`, or `1 Agent Run`), and Chat
and Trajectory show its work live. Run states read Working, Done, Timed Out, Token Limit Reached, Failed or
Cancelled.

**Settings → Plugins.** A new Settings tab lists every plugin in two groups, Built In (Hub, Voice, MCP
and the rest of the bundled `plugins/`) and Installed: name, version, source, an on/off switch, its
Background Processes with their state, what the plugin uses, and its settings form. Buttons: Add Plugin,
and Update and Remove for installed plugins. Add Plugin takes a git URL. Plugin settings move here from
Settings → Configuration, which keeps only kollab's own settings, so each field shows in one place.
Installing, updating, removing and turning plugins on or off work only from a page opened on this
computer; over PhoneLink the tab is read-only and says `Manage plugins on this computer`.

Background process states: `Running · 2 h`; `Stopping · 12 min left`; `Running · No Heartbeat for 6 min` (a process that
promised a heartbeat and went quiet); `Exited`; `Stopped` (stopped by you); `Stopped After 5 Crashes ·
View Log`. The list row shows the same text. A running process offers Restart and Stop; a stopped, exited
or crash-stopped one offers Start.

**Add sheet.** Shown before anything is copied or run:

```
Add PR Chain?
From            github.com/<owner>/kollab-pr-chain (main)
                commit 3f2a9c1e8d0b4c6a9e7f2b1d5c3a8e9f0b6d4c2a · files 9b1c04e2
Adds            a right-menu row and a page · code that runs in every agent · engine routes
                1 background process · 1 slash command · 6 agents · no hooks · no MCP servers
Through Kollab  runs agents with your loadouts
                agents that run tools without asking; they can do anything you can
                its loop holds the secret GITHUB_TOKEN; its stage agents run without it, as you
On Its Own      runs gh, git, codex, claude, python3 and pgrep; its coding agents use a shell without asking
                reads ~/.kollab/config.json to stop a chain when it changes
                talks to github.com and raw.githubusercontent.com
Limits          250,000 tokens and 1 hour per agent run · 4 runs at once
                Daily Token Cap 2,000,000 tokens: kollab agent runs plus the usage the plugin reports;
                unreported spend is not capped
Kollab refuses requests made through kollab that the plugin did not declare. It cannot stop plugin
code, or an agent with a shell, from doing more: they run as you, with your files and keys.
Add only plugins you trust.
[Add Plugin]  [Cancel]
```

Every update shows the same sheet with the commit range and the folders that changed, and waits for a
click. Nothing updates by itself. `kollab plugin dev <folder>` shows the sheet with
`From  Dev Folder (not pinned)`.

**Turning a plugin off.** With agent runs working, Off asks first: `Turn off PR Chain? 2 agent runs are
working and will be cancelled. Its loop may take up to 40 min to finish its current step.` (`1 agent run
is working` for one) [Turn Off] [Cancel]. While its background processes finish their step, the row reads
`Stopping · 12 min left` and its page stays. When they exit, every open page updates at once: the row
goes, a page that was open shows `PR Chain is off` with a Back to Chat button, and a chat showing its tab
switches to Chat.

**Notices.** A plugin can raise a NoticeBar notice: one line and one button, with no ids or version
numbers in the text: `A pull request is waiting for your review.  [Open]`.

**Composer and chat.** Plugin slash commands appear in the composer palette. A plugin can draw its own
card for a tool or message it owns, such as a PR status card.

**Failures.** A plugin whose code fails while loading keeps its row as `PR Chain  Failed to Load`, and
one that fails while drawing shows `Failed to Render`; both open a small kollab-drawn panel with Retry,
View Error and Turn Off, and everything else keeps working. A plugin whose last boot never finished (the
tab froze or closed during it) is skipped on the next load with the notice `PR Chain was skipped: it
froze the page last time.  [Turn On Again]`. `?plugins=off` in the page URL loads the web UI with no plugin code and Settings →
Plugins says `Plugins are off for this page`. When the daily cap is reached, agent runs fail with
`Daily Token Cap reached. Agent runs start again at 00:00 local time.`

### Terminal

```
kollab plugin add <git-url>[#<ref>]  show the add sheet as a prompt, then install
kollab plugin list                   id, version, on/off, background processes, source
kollab plugin enable <id> | disable <id>
kollab plugin update <id>
kollab plugin remove <id> [--purge]  --purge also deletes the data folder, after a prompt
kollab plugin logs <id> [<process>]
kollab plugin dev <folder>           run a plugin from a working folder, no copy, not pinned
kollab plugin new <id>               copy the bundled hello plugin into ./<id>
kollab --no-plugins                  start with no installed plugins ("Plugins are off for this run")
```

`add`, `update`, `remove`, `enable`, `disable` and `dev` ask on the terminal and refuse when stdin is not
a terminal. That stops scripts by accident; it is not a wall against an agent with a shell (see Tokens). The `--purge` prompt: `Delete PR Chain's data
folder (~/.kollab/plugin-data/pr-chain)? This cannot be undone. [y/N]`. `/plugins` in a chat prints the
same list as `kollab plugin list`. The Plugins part of `/config` shows the same plugins and fields as the
web's Settings → Plugins, from the same list.

## Contract

### Package layout

```
<plugin>/
  kollab-plugin.json      required
  python/                 optional: BasePlugin and engine router; pure Python, vendored deps in python/vendor/
  web/dist/               optional: index.js (built ES module), index.css, and the files they load
  web/src/, package.json  optional: the plugin's source and its build script (not read by kollab)
  bin/                    optional: whatever background processes run
  agents/ skills/ commands/ hooks/hooks.json .mcp.json   optional, existing formats
  settings.json           optional: the plugin's settings section (its values live in the data folder)
```

Package Python loads under a private module name, `kollab_plugin_<id with - as _>`, so `pr-chain` imports
as `kollab_plugin_pr_chain` and two packages never collide.

### Manifest: `kollab-plugin.json`

```json
{
  "format": 1,
  "id": "pr-chain",
  "name": "PR Chain",
  "version": "0.4.0",
  "description": "Takes one GitHub PR at a time through review, fix, audit, UI review, CI and merge.",
  "kollab": ">=0.23.0",
  "python": "python/pr_chain_agent.py:PrChainPlugin",
  "engine": "python/pr_chain_engine.py:make_router",
  "services": {
    "loop": {
      "command": ["python3", "-B", "bin/pr-chain-loop"],
      "autostart": true,
      "restart": "on-failure",
      "stop_timeout_s": 2400,
      "heartbeat_s": 900,
      "secrets": ["GITHUB_TOKEN"]
    }
  },
  "web": { "entry": "web/dist/index.js", "style": "web/dist/index.css" },
  "contributes": {
    "menu": [{ "id": "pr-chain", "label": "PR Chain", "icon": "git-pull-request", "open": { "page": "board" } }],
    "pages": [{ "id": "board", "title": "PR Chain" }],
    "slash": ["pr-chain"]
  },
  "agents": "agents", "skills": "skills", "commands": "commands",
  "hooks": "hooks/hooks.json", "mcp": ".mcp.json",
  "settings": "settings.json",
  "uses": ["agents", "agents:unattended", "secret:GITHUB_TOKEN", "run:gh,git,codex,claude,python3,pgrep",
           "net:github.com,raw.githubusercontent.com", "files:~/.kollab/config.json"]
}
```

| Key | Meaning |
|---|---|
| `format` | the manifest format, `1`; checked after the `kollab` range, so an old kollab reports the version first |
| `id` | `^[a-z0-9][a-z0-9-]{1,39}$`; the install folder and data folder name |
| `name` | Title Case: menu row, sidebar group, Settings |
| `kollab` | version range; `add` refuses a kollab outside it |
| `python` | a `BasePlugin` class that loads in every agent daemon |
| `engine` | `make_router(ctx: PluginContext) -> APIRouter`, mounted under `/plugins/{id}/api/` |
| `services.<name>` | `<name>` matches `^[a-z0-9][a-z0-9-]{0,39}$`; `command` (run from the plugin folder); `autostart`; `restart` (`on-failure` default, `always`, `never`); `stop_timeout_s` (default 30); `heartbeat_s` (optional); `secrets` (granted secrets the wrapper puts in its environment) |
| `web` | the entry module and stylesheet, both inside `web/dist/` |
| `contributes` | everything the plugin adds, by id: `menu` rows, `views`, `pages`, `modals`, `slash` commands and chat `renderers`. The sheet's Adds line and the app's rows come from here, so a row exists even when the bundle fails; the bundle's `k.*` calls bind to these ids, and an undeclared id is ignored with a warning |
| `version`, `description` | strings |
| `agents`, `skills`, `commands`, `hooks`, `mcp`, `settings` | paths inside the plugin, in the formats kollab already reads |
| `uses` | see the next section |

Every path resolves inside the plugin folder (by realpath; a symlink that leads out is refused).
Unknown keys are refused, so a typo is an error and not a silent no-op. `services` is the manifest key;
the UI calls them Background Processes.

### What `uses` means

| Entry | Lets the plugin | Kollab refuses it when undeclared |
|---|---|---|
| `agents` | start agent runs through `ctx.agents` or `POST /plugins/{id}/agents` | yes, on its routes and `ctx` |
| `agents:unattended` | give those runs approval mode `trust_all` (tools run without asking) | yes, on its routes and `ctx` |
| `secret:<NAME>` | read that one secret through `ctx.secrets`, or pass it into an agent run | yes, on its routes and `ctx` |
| `run:<programs>` | run these programs | no |
| `net:<hosts>` | talk to these hosts | no |
| `files:<paths>` | read or write outside its data folder | no |

The sheet's lines come from `uses`, `contributes` and each process's `secrets`; a process secret with no
matching `secret:` entry is refused at add. A request through kollab without its entry fails with 403 naming the missing entry (`pr-chain did not
declare agents:unattended`). That refusal covers only requests that go through kollab's routes and
`ctx`. Plugin code runs as the user, so it could go around kollab; the add sheet says so. When a plugin
runs coding agents of its own (pr-chain's `codex exec` and `claude -p` stages), the sheet says what they
can do under On Its Own.

### Where plugins come from

`add` takes a git repository: an `https://` or `ssh://` URL, or scp-style `git@host:owner/repo`, with an
optional `#<ref>` (`https://github.com/o/r#release/1.2`). A URL that carries a user name or password is
refused, and git's error text is shown with any credentials removed. Local folders are not installed;
they run with `kollab plugin dev`, unpinned and labeled. A ref must match
`^[A-Za-z0-9][A-Za-z0-9._/-]*$` and name exactly one of `refs/heads/<ref>` or `refs/tags/<ref>`.
Kollab runs `git ls-remote -- <url>`, `git clone --no-checkout -- <url> <staging>` and `git -C <staging>
archive <commit>` into a clean folder, so nothing a user typed reaches git as an option and the install
holds no `.git`. The ref resolves to a full commit (40 or 64 hex digits) and the clean folder to a files
hash (sha256 over every path and its contents) before the sheet. `plugins.json`, confirm and every load
check both; a folder whose files no longer match is not loaded and shows `Changed on Disk`. An update
follows the ref recorded at add.

### Where plugins live

| Path | Holds | Written by |
|---|---|---|
| `~/.kollab/plugins/<id>/` | the installed plugin, a clean copy at the pinned commit | `add`, `update` |
| `~/.kollab/plugins.json` | `{"plugins": {"<id>": {"source", "ref", "commit", "files_sha256", "enabled", "granted": [...], "added", "updated"}}, "dev": {"<id>": "<folder>"}}`; on update `granted` becomes the confirmed `uses` | kollab, under `kollabor_config.file_lock` |
| `~/.kollab/plugin-data/<id>/` | the plugin's own state (`KOLLAB_PLUGIN_DATA`); kept on update and remove | the plugin |
| `~/.kollab/plugin-data/<id>/values.json` | the plugin's settings values: the only store for them | kollab (see Settings) |
| `~/.kollab/plugin-data/<id>/run/` | `token` (0600), `<process>.pid`, `<process>.state`, `<process>.log`, `runs.json`, `usage.jsonl`, `usage-<date>.json` | kollab, and `usage.jsonl` appends from the plugin |
| `~/.kollab/plugin-data/<id>@dev/` | a dev run's own data folder; its `values.json` starts as a copy of the installed values | the dev run |

Install and update are one unit: the old folder moves to `<id>.old`, the clean folder is renamed into
place, `plugins.json` is written under its sibling `plugins.json.lock`, and `<id>.old` is deleted. A
failure part-way is rolled back on the next start by restoring `<id>.old`.

A folder in `~/.kollab/plugins/` with a `kollab-plugin.json` is a package and loads only through its
manifest; a loose `.py` file there loads as today. A dev run has its own data folder and its own process
names, so it never touches the installed copy's state; Settings → Plugins marks the installed copy
`Shadowed by a Dev Folder` while the dev run is on.

### Python parts

The in-process parts (`python`, `engine`) can import kollab's own dependencies and pure-Python packages
vendored under `python/vendor/`. Kollab refuses to load a plugin whose vendored top-level names are
already imported or vendored by another plugin; its row shows `Failed to Load` with the clashing name.
Kollab ships as one binary, so `add` never runs pip; a background process is any command and can use its
own interpreter.

Plugin Python obeys the terminal rules: it never writes to stdout or stderr. It logs with
`logging.getLogger(__name__)` and shows text only through `message_coordinator`. A `python` or `engine`
entry that raises while loading is skipped, the process starts without it, and `/plugins` reports
`state: "error"` with the message.

### Where each part runs

| Part | Process | Lifetime |
|---|---|---|
| `python` (`BasePlugin`) | every agent daemon, after the bundled plugins | the daemon |
| `engine` | the engine | while the engine runs |
| `services` | `kollab plugin run-service` wrapper plus its child, detached | until stopped; survives engine and web UI restarts |
| `web` | the browser page | the page |
| agents from `agents.run` | a `kollab --detached` daemon started through the engine's `DaemonPool` | one run |

### Engine routes (`kollabor_engine/routes/plugins.py`)

All sit behind the existing bearer-token middleware.

| Route | Does | Plugin token | Remote page |
|---|---|---|---|
| `GET /plugins` | `{schema: 1, plugins: [PluginEntry]}` (below) | no | yes |
| `GET /plugins/events` | SSE: `plugins-changed` after any install, update, remove, enable or disable | no | yes |
| `ANY /plugins/{id}/api/{path}` | the plugin's router, through one dispatcher to a per-plugin sub-app, so enable and disable need no engine restart | own id | yes, except routes the plugin marks `@ctx.local_only` (pr-chain marks its config and profile writes, which change the commands its chains run) |
| `GET`, `POST /plugins/{id}/events` | the plugin's SSE stream of `{topic, data, ts}`; the engine sets `ts`. Topic `heartbeat` with `{service}` feeds the process state; with no engine, heartbeats are dropped and the state reads unknown | own id | yes |
| `GET /plugins/{id}/usage` | today's `{date, total, cap, remaining}` | own id | yes |
| `GET /plugins/{id}/settings` | read its settings | own id | yes |
| `PUT /plugins/{id}/settings` | save its settings (Settings below) | own id | no |
| `POST /plugins/{id}/agents` → `{run_id, session_id}` at once; `GET`, `DELETE .../agents/{run_id}` | agent runs | own id | yes |
| `POST /plugins/{id}/services/{name}/start`, `/stop`, `/restart` | background processes | own id | no |
| `GET /plugins/{id}/services/{name}/log?tail=200` | the process log, redacted like Agent Output | own id | yes |
| `POST /plugins/install`, `POST /plugins/{id}/update` → `{job_id}`; `GET /plugins/jobs/{job_id}` | clone or fetch into staging, check, then report the sheet facts | no | no |
| `POST /plugins/jobs/{job_id}/confirm` | `{commit, files_sha256}`: refused unless both match the staged copy; then move into place | no | no |
| `POST /plugins/{id}/enable`, `/disable`, `/remove` | manage | no | no |

"Plugin token: no" means only the user's engine token works. "Remote page: no" means the web UI's
`/engine` proxy and the PhoneLink bridge refuse the route for any client that is not on this computer (the proxy sees the real
client address; the engine behind it only ever sees the proxy), and the engine refuses it on requests the
proxy marks as forwarded. A page opened over PhoneLink or the relay can use plugins but never install or
manage them.

### Tokens and what plugin code can reach

- The page's `/config` endpoint hands the user's full engine token to every script on the page, so web
  plugin code holds the user's full engine access, including the management routes from a local page.
  Plugin code on disk can read `~/.kollab/engine.token` too. Decision D1 accepts this; the add sheet says
  plugins run as the user.
- The plugin token (`KOLLAB_PLUGIN_TOKEN`) is minted once per plugin, kept in `run/token`, and given to
  background processes and the engine router. The engine accepts it only on its own id's routes marked
  above. It narrows mistakes and attributes calls; it is not a boundary against plugin code.
- Agent runs are not a wall either. An agent with a shell runs as the user and can read
  `~/.kollab/engine.token` and call the engine itself. The agent file tools gain a read-side deny list
  for token files and `~/.kollab/plugins/` (the existing `is_protected_path` check,
  `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py:271`, covers deletes only); that
  stops the file tools, not a shell. The protection is the default: plugin agent runs ask before running
  a tool unless the plugin declared `agents:unattended` and the user saw it on the sheet. A wall for
  unattended shells needs an OS sandbox (#1037).

### Serving web files

The web UI server serves `GET /plugins/{id}/web/{path}` only from the plugin's `web/dist/` folder, only
files ending in `.js`, `.mjs`, `.css`, `.png`, `.woff2` or `.json`, never a dotfile, and with
`X-Content-Type-Options: nosniff`. The route is registered before the app's catch-all route and uses the
same `_safe_asset` containment check as the app's own files, so a symlink that leaves `web/dist/` is
refused. The web UI sends `Content-Security-Policy: script-src 'self'` plus the import map's hash, so no
inline script or `eval` runs on the page. An HTML file is never served, so nothing in a plugin folder can open
a page of its own on the web UI's origin. Asset URLs carry `?v=<hash of the file>`.

### Engine-side context (`kollabor_plugins.context.PluginContext`)

Passed to `make_router(ctx)`:

| Member | Does |
|---|---|
| `ctx.id`, `ctx.root`, `ctx.data` | id, plugin folder, data folder |
| `ctx.settings.get()`, `ctx.settings.save(values)` | its settings (the one writer, below) |
| `ctx.events.publish(topic, data)` | SSE to its web code |
| `ctx.notify(text, button=None)` | a NoticeBar notice in every open web UI |
| `await ctx.agents.run(...)` | an agent run; needs `agents` |
| `ctx.secrets.get(name)` | a secret from kollab's secret store; needs `secret:<name>` |
| `ctx.services.status()` | its own background processes |
| `ctx.lock(name)` | a `kollabor_config.file_lock` lock in its data folder |

Background processes get `KOLLAB_PLUGIN_ID`, `KOLLAB_PLUGIN_ROOT`, `KOLLAB_PLUGIN_DATA`,
`KOLLAB_ENGINE_URL` and `KOLLAB_PLUGIN_TOKEN`, and read settings from `<data>/values.json`, the file every save writes.

### Agent runs

```json
{
  "brief": "Read brief.md and follow it exactly.",
  "folder": "/abs/path/to/worktree",
  "agent": "reviewer",
  "loadout": "luna-high",
  "tools": ["file-read", "file-grep"],
  "approval": "ask",
  "secrets": [],
  "env": { "GH_CONFIG_DIR": "/abs/path/to/empty-gh" },
  "timeout_s": 3600,
  "max_tokens": 250000,
  "labels": { "pr": "970", "stage": "review" }
}
```

- `POST /plugins/{id}/agents` answers at once with `{run_id, session_id}`. The run's result is read with
  `GET .../agents/{run_id}`: `{status: "running" | "done" | "timeout" | "capped" | "failed" | "cancelled",
  final_text, transcript, usage: {input, output, cost, model}}`.
- `folder` goes through `folder_or_400`. `approval` is `ask` when absent; `trust_all` needs
  `agents:unattended`. Each name in `secrets` needs `secret:<NAME>`, and secrets are refused for a run
  whose tools include a shell or network tool.
- Plugin run daemons are detached: an engine shutdown or crash leaves them running, and the next engine
  adopts each live one from `runs.json` by pid and start time, or stops it when it is past its deadline.
- Environment: built from an allowlist (`PATH`, `LANG`, `LC_*`, `TERM`, `TMPDIR`, `USER`, `SHELL`), with
  `HOME` set to a scratch folder inside the plugin's data folder and `KOLLAB_CONFIG_DIR` set to the real
  config folder so the daemon still finds its loadouts; then `env`, whose names must not be
  secret-looking (the filter the Code terminal uses, #987), `HOME`, `PATH` or start with `KOLLAB_`,
  `PYTHON`, `LD_` or `DYLD_`; then each granted secret. `SSH_AUTH_SOCK` and every other inherited
  variable stay out.
- The engine starts the daemon through `DaemonPool` with `KOLLAB_USAGE_LANE=plugin:<id>`,
  `KOLLAB_USAGE_PR=<labels.pr>` (usage ledger, #723), `KOLLAB_RUN_DEADLINE` and `KOLLAB_RUN_MAX_TOKENS`.
  The daemon refuses provider requests past either limit, at the same dispatch-time gate `/goal` budgets
  use (`kollabor/goals/`), so the limits hold while the engine is down. The engine sends the brief and
  stops the daemon at `turn_complete`; the conversation stays on disk.
- Each run is a row in `run/runs.json` (run id, session id, daemon pid and start time, deadline, cap,
  status, tokens so far, and the result when it ends), rewritten whole with the atomic write helper
  under a sibling `runs.json.lock`. The daemon writes its own end state and result when it finishes, is
  refused at the gate or exits, so a run that ends while no engine runs still records how it ended; a
  starting engine marks rows whose daemon is gone as `failed`. Finished rows stay for the daily count.
- A plugin runs at most 4 agents at once (`max_concurrent_runs`); the next start gets 429. A run is
  admitted against the day's counted tokens plus the tokens of runs still going.
- `tools` goes through the agent bundle's tools allowlist. That allowlist has open gaps (#889 to #897),
  so agent runs ship only after those issues close.

### Usage and the daily cap

The engine keeps one counter per plugin per day in `run/usage-<date>.json` (local date):
`{date, total, run_ids}`, written atomically under its sibling lock, while `run/usage.offset` records how
far `run/usage.jsonl` has been read. It adds each finished agent run once (by run id) and each complete
line a plugin appends to `run/usage.jsonl` (`{ts, model, input, output, labels}`) to the day of that
line's own `ts`; appending works with or without an
engine. `max_tokens_per_day` (default 2,000,000) refuses the next agent run with 429 once the counter
reaches it, and resets at 00:00 local time. Tokens a plugin spends without appending them are not
counted; pr-chain appends one line per command stage from the transcript it already reads, and its loop
reads `GET /plugins/pr-chain/usage` before each stage, sums today's `usage.jsonl` lines itself when no
engine answers, and starts no stage past the cap or when it cannot tell.

### Web plugin API

`web/dist/index.js` default-exports a `definePlugin` call from `@kollab/plugin`:

```tsx
import { definePlugin } from "@kollab/plugin";
import { Board, PullRequests, Issues, Spend, Config } from "./pages";

export default definePlugin((k) => {
  k.page({
    id: "board",
    title: "PR Chain",
    tabs: [
      { id: "board", label: "Board", render: Board },
      { id: "prs", label: "Pull Requests", render: PullRequests },
      { id: "issues", label: "Issues", render: Issues },
      { id: "spend", label: "Spend", render: Spend },
      { id: "config", label: "Config", render: Config },
    ],
  });
  k.menu("pr-chain", { count: { path: "/badge", refreshOn: "chain" } });
});
```

Every `render` is a React component that receives `{ k, session }` as props (`session` only inside a
`view`). The `/badge` route answers `{text, hint}`. Paths in `k.api`, `k.useApi` and `count.path` are
relative to `/plugins/{id}/api/`.

Contribution points. Each is one existing component that also reads the plugin registry:

| Call | Shows in | Existing owner |
|---|---|---|
| `k.menu(id, {count})` | a live count on a declared right-hand menu row | menu rows in `SessionPanel.tsx` |
| `k.view({id, label, icon, render, needs_folder?})` | a main-area tab for the open chat | `VIEWS` in `SessionPanel.tsx` |
| `k.page({id, title, tabs})` | the whole main area with a plugin tab bar | main column in `App.tsx` |
| `k.modal({id, title, tabs})` | a dialog with tabs; full screen on a phone | `components/ui/dialog` |
| `k.settings({render})` | Settings → Plugins → this plugin, under its generated form | `panel-model.ts`, `PanelHost.tsx` |
| `k.command({name, description, run})` | the composer palette | the composer's slash palette |
| `k.renderer({tool or tag, render})` | a chat card for that tool or message tag | `Thread.tsx` |
| `k.notice({text, button})` | NoticeBar | `NoticeBar.tsx` |

`open` is `{page}`, `{modal}`, `{view}` (switches the open chat to that tab) or `{url}`.

What `k` gives a plugin:

- `k.api.get`, `post`, `put`, `delete`: its own engine routes, with auth, through `/engine` when the
  page is remote.
- `k.useApi(path, {refreshOn?, everyMs?})`: a React hook that reads one of its routes and refreshes on
  an event topic or a timer. `k.events.on(topic, fn)` for anything else.
- `k.settings.get()` and `k.settings.save(values)`: the same store Settings → Plugins edits.
- `k.agents.open(sessionId)` opens an agent's Chat. `k.navigate(target)`, `k.copy(text)`,
  `k.toast(text)`.
- `k.ui`: the app's own components (Button, Tabs, Dialog, Sheet, Select, Switch, Input, Label,
  Skeleton, Tooltip, HoverHelp, ScrollArea, FieldRenderer, GemAvatar), so a plugin looks like kollab.
  The theme's CSS variables are on `:root` in both themes.

Plugin UI follows the app's rules: text from GitHub, agents or any outside source renders as text or
through the app's Markdown renderer, never as HTML; controls are real buttons and inputs with labels;
streamed output sits in an `aria-live="polite"` region. Each plugin view mounts inside
`<div data-plugin="<id>">` and an error boundary; plugin CSS scopes under that attribute, and
`kollab plugin add` warns when `index.css` has rules outside it.

Loading: at boot the app reads `GET /plugins`, `import()`s every enabled entry in parallel and awaits
each plugin function. A 5 s limit covers the import and that await; it cannot stop a synchronous loop
in plugin code, which freezes the tab, and `?plugins=off` is the way back in. Retry imports
`index.js?v=<hash>&retry=<n>` so the browser does not hand back the failed module, and a timed-out
import that resolves later is ignored. On `plugins-changed` the page reads `GET /plugins` again and
adds or removes rows, tabs and pages without a reload.

One React: the app build adds a `plugin-runtime` entry that re-exports the app's own `react`,
`react-dom`, `react/jsx-runtime` and `@kollab/plugin`, and `index.html` carries an import map from those
names to the built files. A plugin build marks them external. `add` and `dev` refuse a bundle that
carries its own React (`web/dist/index.js bundles its own React: mark react as external`).

The hello plugin template carries the dev loop: `package.json` with `npm run build` (Vite library build
into `web/dist/`) and `npm run dev` (the same build in watch mode), and `web/src/kollab-plugin.d.ts`,
generated from `frontend/src/plugins/sdk.ts`. Under `kollab plugin dev`, a page reload picks up the
rebuilt `web/dist/`; a change under `python/` remounts the engine router and restarts the plugin's
background processes; the `python` part reaches agents when they restart.

### Settings

A plugin's settings are one section in the shape `get_config_widgets()` already returns:
`{"title": "PR Chain", "widgets": [{"type", "label", "config_path", "help", ...}]}`. For a package,
`config_path` is relative to the plugin; a built-in plugin's widgets keep the full paths they already
use, and the conversion adds nothing to them. `settings.json` holds exactly that section, so a package plugin and a `BasePlugin`
declare settings the same way. Values are `{"<config_path>": value}` in `values.json`.

```json
{
  "title": "PR Chain",
  "widgets": [
    { "type": "spinbox", "label": "Max Threads", "config_path": "loop.threads", "help": "Chains that run at once.",
      "min_value": 1, "max_value": 4, "step": 1, "default": 1 },
    { "type": "checkbox", "label": "Build Issues Into PRs", "config_path": "issues.build", "default": true,
      "help": "Off also stops scouting for new issues; open PRs keep flowing." }
  ]
}
```

Values have one writer, `kollabor_plugins.settings.save(id, values)`, which checks each value against
its widget and writes the store with kollab's atomic write helper while holding a sibling `.lock` file
(never a lock on the file that gets renamed). `PUT /plugins/{id}/settings`, `k.settings.save`,
`ctx.settings.save`, Settings → Plugins and the terminal's `/config` all call it; the config panel's save
path sends plugin rows there instead of to `config.json`.

- An installed plugin's store is `~/.kollab/plugin-data/<id>/values.json`, never
  `~/.kollab/config.json`, so a save cannot disturb anything that watches `config.json` (pr-chain stops
  itself when `config.json` changes during a chain).
- A built-in plugin keeps its existing `plugins.<name>.*` keys in `~/.kollab/config.json`.
- On and off go through `set_enabled(id, on)`, which writes `plugins.json` for installed plugins and the
  existing enable key for built-in ones. `enabled` is not a setting.

A plugin whose `uses` includes `agents` also gets kollab's Daily Token Cap
(`max_tokens_per_day`, default 2,000,000) and Max Agent Runs At Once (`max_concurrent_runs`, default 4),
stored the same way.

### One list of plugins and their settings

The configuration menu's own scanner goes (`_discover_plugin_modules`, `_find_plugin_class`,
`get_available_plugins` and `get_plugin_config_sections` in `config_widgets.py`). One function on the
registry replaces it, and `GET /plugins` serves the same entries:

```
PluginEntry = {id, name, version, kind: "built-in" | "installed" | "dev", source, commit, enabled, shadowed,
               state: "ok" | "error" | "off" | "stopping" | "changed-on-disk" | "skipped", error, settings: [make_field fields],
               web: {entry, style, hash} | null, contributes: {menu, views, pages, modals, commands},
               services: [{name, state: "running" | "stopping" | "exited" | "stopped" | "crash-stopped" | "unknown",
                           desired, pid, since, restarts, heartbeat}], uses, granted}
```

`kollabor_plugins.registry.plugin_entries()` lists what the loader found: the bundled `plugins/`, loose
modules in `~/.kollab/plugins`, and every package, on or off. Nothing is matched by file name or class
name. `settings` is the plugin's section turned into panel fields with the conversion the `config` panel
already applies to `get_config_widgets()` sections, with current values from the plugin's store. Fields
that do not apply to a kind are `null`. The terminal's `/config` builds its Plugins part from it, the web
reads it from `GET /plugins`, and the `config` panel stops adding plugin sections. `PluginConfigManager`
folds into it; its readers (`kollabor_config/loader.py:310-315` and `:770`) move in the same change.

### Background processes

Each background process runs under a small wrapper, `kollab plugin run-service <id> <process>`, started
with `kollabor.binary.kollab_argv()` in its own session:

- **Identity.** The supervisor takes `try_lock(run/<process>.lock)`, then reads `run/<process>.pid`
  (`{wrapper: {pid, start_time, nonce}, child: {pid, pgid, start_time}}`). The process counts as running
  when the wrapper is alive with a matching start time and nonce, or when the wrapper is gone but its
  recorded child is alive; then nothing new starts until that child exits. Nothing is found by scanning
  other processes, so a stage child or a test install can never be mistaken for the service. Otherwise
  the supervisor starts a wrapper with a fresh nonce, and the wrapper writes the pid file before and
  right after it starts the command. The wrapper holds `run/<process>.lock` for its whole life and exits
  at once if another wrapper holds it, so the supervisor's `try_lock` failing means a wrapper is running.
  The wrapper is the only writer of the pid and state files and writes them atomically; its nonce is in
  its command line, so `ps` shows which start it is.
- **Desired state.** `run/<process>.state` holds `desired` (`running` or `stopped`) and the exit history.
  The tick starts only processes whose desired state is running and that are not crash-stopped. Start
  sets running; Stop, Turn Off and the crash limit set stopped; an `on-failure` process that exits 0 stays
  `Exited` until Start.
- **Environment.** The wrapper runs with the `PATH` captured when the plugin was enabled, as
  `kollab service` does, plus the process's granted `secrets`. `add` resolves each `run:` program on that
  `PATH`, and a start fails with `PR Chain needs codex: not found on PATH` when one is missing.
- **Restarts and exits.** The wrapper runs the command as its child, records each exit code in
  `run/<process>.state`, and restarts by the `restart` rule with backoff 2 s, 4 s, 8 s and so on up to
  5 min. Five crashes in 10 minutes stop the retries: the wrapper exits, Settings shows `Stopped After 5
  Crashes · View Log`, the menu count reads `Stopped`, and a notice says `PR Chain stopped after 5
  crashes.  [View Log]`. Restarts need no engine.
- **Starting wrappers.** The supervisor runs every 30 s while an engine runs, and once whenever a kollab
  process starts: the engine, an agent daemon (including the one `kollab service install` starts at
  login) or a `kollab plugin` command. After a reboot, background processes come back with the first of
  those.
- **Running and heartbeat.** With `heartbeat_s`, the state reads `Running · No Heartbeat for <n> min` once
  a `heartbeat` event (`{service: "<name>"}`) is that late.
- **Stopping.** The supervisor sends SIGTERM to the wrapper, which passes it to its child and stops
  restarting. The child gets up to `stop_timeout_s` to finish its current step, start nothing new and
  exit; then the wrapper sends SIGKILL to whatever in its process group is still alive. Processes the
  child moved into their own session are its own to stop.
- **Logs.** The wrapper owns `run/<process>.log`: it writes the child's output, and at 10 MB renames the
  file and opens a new one, keeping 3.

Disable and remove first ask about live agent runs, cancel them, then stop the plugin's processes.
Running agent daemons keep a plugin's `python` part until they restart: after Off or Update, Settings →
Plugins shows `3 agents still run the old version` with Restart Agents, and each daemon picks up the
change when it restarts.
Update stops processes the same way, swaps the folder, and starts them again.

### pr-chain as a plugin

The plugin lives in its own repository and installs with `kollab plugin add <git-url>`. Until that
repository exists, step 4 develops it with `kollab plugin dev`.

| pr-chain today | As a plugin |
|---|---|
| the `pr-chain` command, `ui_stack.py`, `ui_shoot.py` and the shared helpers it calls from outside its folder (`fold.py`, `land_pr.py`, `kit.py`) | all inside the plugin: `bin/` and `python/`, as the plugin's own copies (the shared ones stay for other tools); every path resolves from `KOLLAB_PLUGIN_ROOT` or `KOLLAB_PLUGIN_DATA`, apart from the declared `~/.kollab/config.json` read |
| the repo, checkout, Python interpreter and owner accounts set in `kit.py` and the script | plugin settings `repo`, `checkout`, `python` and `owners` (empty by default; the plugin lands nothing until it is set) |
| the stage prompts | seeded into the data folder from the plugin's defaults on first run, so edits survive updates |
| `STATE.json` written in place | written with a temp file and rename (a small atomic-write function vendored in `python/`, since the loop runs as plain `python3`) |
| the config tripwire: a chain stops when `~/.kollab/config.json` changes | kept, comparing key by key and ignoring the keys kollab stamps itself |
| the `pr-chain` command from a terminal | also `/pr-chain <args>` in a chat, through the plugin's `python` entry (a `BasePlugin` command) |
| the loop kept alive with `nohup` | background process `loop`; it gains a SIGTERM handler that starts no new lap and passes SIGTERM to its chains, which record each stage's process group in their claim and stop it; `stop_timeout_s` 2400 covers one stage. A `STOP` file shows as `Stopped by STOP File` with Clear |
| `board-config.json` (thread cap) and the `NO-BUILD` file (issue building off) | plugin settings `loop.threads` and `issues.build` (on unless `NO-BUILD` existed); the Config tab and Settings → Plugins edit the same values |
| `models.json`, `profiles.json`, `commands.json` | files in the data folder, edited by the Config tab through the plugin's routes |
| `rates.json` and the downloaded price cache | in the data folder; a refresh fetches from `raw.githubusercontent.com`, declared in `uses` |
| `ledger.jsonl`, `prs/<n>/STATE.json`, `issues/<n>/`, locks, claims, logs, the `STOP` file | the same files under `~/.kollab/plugin-data/pr-chain/`; ledger rows' absolute paths rewritten on import |
| the board's own web server and port | an `engine` router and a `web` page with the same five tabs |
| board JSON routes `/api/state`, `/api/prs`, `/api/pr/<n>` (and `/diff`), `/api/issues`, `/api/issue/<n>`, `/api/spend`, `/api/config` (GET, POST) | the same handlers with the old `/api` prefix dropped inside the router: `/plugins/pr-chain/api/state`, `/plugins/pr-chain/api/prs` and so on, with the same query parameters (`?pr=`, `?state=`), plus `/badge` |
| the Board's queue and its UI and owner columns; loop and chain liveness | `pr-chain pick --all`; `ctx.services.status()` and the claims folder |
| the menu row's "needs you" | the PRs in `AWAIT-HUMAN` or `NEEDS-HUMAN` |
| the Board's 3D stage map (three.js) | the same stage map, using `k.ui.GemAvatar` or a three.js copy bundled in `web/dist/` |
| Agent Output from the bigger of each stage's `err.txt` and `out.jsonl`, polled every 3 s | the same, passed through one redaction function before it is served: token URLs, `Authorization` values, keys and the engine token are replaced with `[redacted]` |
| stage agents as command lines (`codex exec`, `claude -p`) with every tool and no approval prompts | the same, said plainly on the add sheet; a stage can instead name a kollab loadout, which runs through `agents.run` with a read-only tool list for review, audit and UI stages and shows in the sidebar |
| spend from transcripts and a price table | unchanged, plus one line per command stage appended to `run/usage.jsonl` for the daily cap |
| a relay process to reach the board from other devices | not needed: the web UI already serves remote devices with auth |
| `routes/pr_chain.py`, `pr_chain.py` and `components/pr-chain/` in kollab (#1008) | removed from core once the plugin page has parity |

The port adds what today's script lacks: each command stage gets the agent-run environment allowlist
with a scratch HOME (no gh or ssh config), `GIT_CONFIG_NOSYSTEM=1` and `GIT_TERMINAL_PROMPT=0` (no
system credential helpers), an empty `GH_CONFIG_DIR`, and no `GH_TOKEN`, `GITHUB_TOKEN`,
`SSH_AUTH_SOCK`, `KOLLAB_PLUGIN_TOKEN` or provider keys. Stage shells still run as the user and can reach
whatever the user's keychain and files allow; kollab does not sandbox them (#1037). The plugin's own code
is what pushes, labels, comments and merges.

Parity means that for the same data every route above answers what the current board answers, and
screenshots of the five tabs match at 1440, 820 and 390 px wide in dark and light.

### Cutover from the running tool

Two loops never run on the same PRs. With the owner's yes:

1. `pr-chain stop` writes the `STOP` file; the running chains finish their stage and the loop exits.
2. Kollab imports once into `~/.kollab/plugin-data/pr-chain/`: from the state folder `prs/`, `issues/`,
   `ledger.jsonl` (absolute paths rewritten) and `logs/`; from the config folder `models.json`,
   `profiles.json`, `commands.json`, `prompts/`, `rates.json`, the price cache, the GitHub read cache and the skip lists `exclude.txt` and
   `exclude-issues.txt`, which the plugin's pick reads; the thread cap from
   `board-config.json` and the issue switch from `NO-BUILD` into the plugin's settings. Worktrees
   (`prs/**/wt/`), `STOP`, `locks/`, `claims/` and scratch homes are skipped, worktrees come back on
   demand, and the `ui_seed` paths in `STATE.json` are rewritten with the ledger's. A marker file in the
   data folder records the import, so it never runs twice.
3. The old board runs on a second copy of the data, through new `PR_CHAIN_WORK`, `PR_CHAIN_CONFIG` and
   `PR_CHAIN_SCRIPT` overrides (today it reads fixed home-folder paths), so nothing it writes reaches
   either original, while the plugin page is compared with it.
4. With parity shown, the plugin's loop is turned on. The old loop, board and relay are retired: stopped
   for 7 days with nothing deleted, so starting the old loop again is the way back if the plugin has to
   be turned off.

### Owning files

| File | Change |
|---|---|
| `packages/kollabor-plugins/src/kollabor_plugins/packages.py` | new: source checks, manifest checks, add, update, remove, `plugins.json` |
| `packages/kollabor-plugins/src/kollabor_plugins/services.py` | new: supervisor and the `run-service` wrapper |
| `packages/kollabor-plugins/src/kollabor_plugins/settings.py` | new: the one settings writer and `set_enabled` |
| `packages/kollabor-plugins/src/kollabor_plugins/context.py` | new: `PluginContext` |
| `packages/kollabor-plugins/src/kollabor_plugins/registry.py`, `discovery.py`, `plugin_sdk.py` | `plugin_entries()`; packages load through their manifest; loose modules as today |
| `packages/kollabor-tui/src/kollabor_tui/config_widgets.py` | drop the scanner; build the Plugins part from `plugin_entries()` |
| `kollabor/panels/config.py` | stop adding plugin sections to Settings → Configuration; send plugin rows' saves to the settings writer |
| `packages/kollabor-config/src/kollabor_config/plugin_config_manager.py`, `loader.py` | fold into `plugin_entries()` |
| `kollabor/application.py`, `kollabor/cli.py` | load package `python` entries; `kollab plugin ...`, `run-service`, `--no-plugins` |
| `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py` | a read-side deny list for token files and `~/.kollab/plugins/` |
| `packages/kollabor-webui/frontend/src/components/ui/dialog.tsx` | a full-screen phone variant with a scrolling body |
| `kollabor/goals/` | honor `KOLLAB_RUN_DEADLINE` and `KOLLAB_RUN_MAX_TOKENS` at the dispatch gate |
| `packages/kollabor-engine/src/kollabor_engine/routes/plugins.py`, `plugin_agents.py`, `server.py` | routes, agent runs, usage counter, supervisor tick |
| `packages/kollabor-engine/src/kollabor_engine/daemon_pool.py` | take a run's own environment instead of copying the engine's; leave plugin run daemons running when the engine stops |
| `packages/kollabor-webui/src/kollabor_webui/server.py` | serve `/plugins/{id}/web/*` from `web/dist/` only |
| `packages/kollabor-webui/frontend/src/plugins/` | new: loader, registry, `sdk.ts`, error boundary |
| `packages/kollabor-webui/frontend/vite.config.ts`, `index.html` | `plugin-runtime` entry and import map |
| `SessionPanel.tsx`, `App.tsx`, `panel-model.ts`, `PanelHost.tsx`, `Thread.tsx`, `NoticeBar.tsx`, `AppSidebar.tsx` | read the registry |
| `bundles/plugins/hello/` | new: the smallest plugin using every part; test fixture and `kollab plugin new` template |

### Build order

1. Packages, the wrapper and supervisor, the one plugin list with `/config` reading it, the settings
   writer and the CLI, with the hello plugin's Python, settings and one background process. Terminal only.
   The hello plugin's web parts land in step 3; until then its tests use a committed `web/dist/` fixture.
2. Engine routes except agent runs (`/plugins`, events, settings, background processes, install and
   management), the agent file tools' read deny list and the `web/dist/` file route, each with its tests.
3. The state mockups, then the web loader, registry and the eight contribution points, Settings →
   Plugins and the add sheet; the pr-chain plugin with its command-line stages, developed with
   `kollab plugin dev`.
4. Agent runs, once #889 to #897 are closed, and pr-chain's option to run a stage on a kollab loadout.
5. Cutover, then removal of the #1008 reader and dialog.

Steps 2 and 3 share no files and can run at the same time once step 1 lands. Every step lands as its own
PR, goes through the design review swarm when it changes a surface, and gets an independent code review.

## Scenarios

1. If a plugin is enabled and declares a menu row, then the row shows in the right-hand menu of every
   chat and opens its target. If it is turned off, every open page drops its row, page and tab without a
   reload, a page that was open shows `PR Chain is off` with Back to Chat, and a chat on its tab goes to
   Chat.
2. If a plugin's import or awaited function throws or passes 5 s, then its row shows Failed to Load
   (Retry, View Error) and the rest of the app works. If its view throws while drawing, that view shows
   Failed to Render. If Retry works, the row returns.
3. If background process `loop` exits non-zero, then its wrapper restarts it after 2 s, 4 s, 8 s and so
   on, with or without an engine; after five crashes in 10 minutes it stays stopped, Settings and the
   menu row say so and a notice appears. If it exits 0 with `restart: on-failure`, it shows `Exited`.
4. If the engine restarts while `loop` runs, then `loop` keeps running and the new engine finds its
   wrapper by pid, start time and nonce. A stage child or another install's process never matches.
5. If a plugin starts an agent run with `approval: "trust_all"` without `agents:unattended`, then it gets
   403 `pr-chain did not declare agents:unattended`. With the entries declared and granted, the agent
   shows under PR Chain in the left sidebar while it runs.
6. If a run passes `timeout_s` or `max_tokens`, then its daemon refuses further provider requests and the
   run reads `timeout` or `capped`, even while the engine is down. A run that finishes first reads `done`.
7. If an update is available, then `update` shows the sheet with the commit range and changed folders,
   and nothing changes until the user confirms the commit the sheet showed; the old version keeps running
   meanwhile.
8. If two plugins register the same slash command, then the one enabled first keeps it and the other's
   Settings row says `/deploy is taken by Deploy Bot`. Turning off the first frees it for the second.
9. If the page is open on a phone through PhoneLink, then plugins load and work, `k.api` goes through
   `/engine`, and Settings → Plugins is read-only with `Manage plugins on this computer`.
10. If a plugin needs a newer kollab than the one running, then `add` refuses with `PR Chain needs kollab
    0.23.0 or newer; this is 0.22.1`.
11. If the page URL has `?plugins=off` or kollab starts with `--no-plugins`, then no plugin code runs in
    that page or process.
12. If a stage agent appends to its transcript, then the Board's Agent Output shows the new text within
    5 s without a page reload, with any token or key in it shown as `[redacted]`.
13. If a plugin's counted tokens for the day reach its Daily Token Cap, then its next agent run fails
    with 429 and the cap message; at 00:00 local time runs start again.
14. If a plugin is installed, then its settings show in the terminal's `/config` and the web's Settings
    → Plugins with no change to kollab's code; if it is removed, they leave both. A built-in plugin shows
    in both lists too, without Update or Remove.
15. If an agent's shell runs `kollab plugin add`, then it refuses because stdin is not a terminal. An agent
    with a shell that reads `engine.token` and calls the engine itself is not stopped, which is why plugin
    agent runs ask before each tool unless the sheet showed `agents:unattended`.
16. If Turn Off is pressed while the loop is in a stage, then the row reads `Stopping` with the time left,
    the page stays, and both go when the loop exits; a second Turn On while it stops is refused with
    `PR Chain is still stopping`.
17. If someone edits a file in `~/.kollab/plugins/pr-chain/`, then the next load refuses the plugin with
    `Changed on Disk` until it is updated or added again.
18. If a PR title holds `"><img src=x onerror=alert(1)>`, then every plugin view shows it as plain text.

## Failure modes

1. **Web bundle throws or fails to render.** A per-plugin error boundary and a 5 s limit on the import
   and the awaited function; the app boots without it. A synchronous hang freezes the tab; recovery is
   a reload with `?plugins=off`.
2. **Engine router or `python` entry fails while loading.** That part is skipped, the process keeps
   running, and `/plugins` reports `state: "error"` with the message.
3. **Background process crash loop.** The wrapper's backoff and five-crash stop, a notice, the menu count
   and the log one click away, with or without an engine.
4. **Runaway agent spend.** Per-run limits enforced by the daemon itself, at most 4 runs at once, and a
   Daily Token Cap of 2,000,000 tokens a day by default over runs and reported usage. Every usage ledger
   row names `plugin:<id>`. Unreported tokens of a plugin's own agents are not capped, and the sheet says
   so.
5. **Indirect prompt injection.** PR text, diffs and comments are written by PR authors and can carry
   instructions. Kollab agent runs default to `approval: "ask"`, with a scratch HOME and an allowlisted
   environment; unattended runs need `agents:unattended`, which the sheet shows as able to do anything
   you can. pr-chain's command stages get no GitHub credentials and a scratch HOME, and only its own code
   writes to GitHub. An OS sandbox for unattended shells is the missing wall (#1037).
6. **A malicious or compromised plugin.** Install is the trust decision. The sheet names the pinned
   commit, every kind of part, what kollab refuses and what the plugin does on its own. Every update asks
   again and confirms the exact commit; management needs a person on this computer. `kollab plugin
   disable`, `--no-plugins` and `?plugins=off` turn plugins off.
7. **Hostile install source.** Only git sources with a checked ref are accepted, nothing typed reaches
   git as an option, the ref resolves to a commit before the sheet, and confirm checks the commit and
   the manifest hash against the staged copy.
8. **Served files.** Only `web/dist/` files of allowed types are served, never HTML or dotfiles, with
   `nosniff`. Manifest paths resolve inside the plugin folder.
9. **Remote callers.** Plugin routes sit behind the engine's token middleware; management routes also
   refuse callers that are not on this computer.
10. **Engine down.** Background processes and their restarts keep going, agent-run limits hold, usage
    lines wait in `usage.jsonl`, and plugin views show the app's engine-down state.
11. **Orphaned runs and half-finished steps.** Turning a plugin off or removing it cancels its live agent
    runs after a confirm, and stopping gives each process `stop_timeout_s` to finish its step.
12. **Lost results.** Every run's state and result are written to `runs.json` with a temp file and rename
    as they change, so a restart loses nothing.

## Decisions

| # | Decision | Who | Why |
|---|---|---|---|
| D1 | Plugin code is trusted and runs as the user, web code included (it can read the page's engine token). No sandbox. | maintainers, owner-approved | Terminal plugins already run in-process. An iframe would cover only the web half while the Python and process halves keep full access, and it would block shared components, gems and chat cards. VS Code extensions, Claude Code plugins and DeepSeek Harness plugins also run with the user's access. |
| D2 | Web plugins are ES modules loaded with `import()` that share the app's React through an import map. | maintainers, owner-approved | One React instance and no Node at install time. DeepSeek Harness serves each plugin's `client.js` and shares React as one platform module the same way. |
| D3 | Plugin entries live in the right-hand menu and open as a tab, a modal with tabs or a page with tabs; eight fixed contribution points, each an existing component. | the owner, for the menu and the three targets; maintainers, owner-approved, for the rest | One design system; nothing injects markup into arbitrary places. Chat cards and custom settings stay because the owner asked for plugins that can add anything. |
| D4 | Background processes run under a kollab wrapper that owns restarts, exit codes and logs. | maintainers, owner-approved | Crash isolation and any language; restarts and exit codes survive engine restarts; the pid file names the wrapper, so nothing is found by guessing. |
| D5 | Plugin agents are real kollab sessions on the user's loadouts, with limits the daemon enforces. | maintainers, owner-approved | Live Chat and Trajectory, the usage ledger, tool allowlists and approval modes come for free, and limits hold with no engine. |
| D6 | One settings shape (the `get_config_widgets()` section), one writer, and installed plugins' values in their data folder. | maintainers, owner-approved | `/config` already draws that shape; a separate store keeps saves away from `config.json`, which pr-chain watches. |
| D7 | Installs come from git, pinned to a resolved commit; folders only run through `dev`. | maintainers, owner-approved | A pinned commit keeps the sheet true; a folder has no commit to pin. |
| D8 | No new project-folder plugins; today's import of them is bug #1035. | maintainers, owner-approved | They need a folder-trust design the goal does not need. |
| D9 | `uses` is a declaration. Kollab's routes and `ctx` refuse undeclared agent runs, unattended runs and secrets; nothing stops plugin code that goes around kollab, and the sheet says so. | maintainers, owner-approved | The refusal catches mistakes and attributes calls; calling it more than that would make the sheet untrue. |
| D10 | Every update asks and confirms the exact commit and files hash; management works only from this computer; plugin agent runs ask before each tool unless unattended runs were declared and shown. | maintainers, owner-approved | Changed code that holds a granted secret is a new trust decision. An agent with a shell can do what the user can, so the honest protection is the default, not a check it can walk around. |
| D11 | Agent runs default to 250,000 tokens, 1 hour, 4 at once and 2,000,000 tokens a day per plugin. | maintainers, owner-approved | An unattended loop with no cap can drain a week of usage; the defaults show on the sheet and change in Settings. |
| D12 | One discovered list of plugins and their settings feeds `/config` and Settings → Plugins; the configuration menu's scanner goes. | the owner | Plugin settings must be discovered, not hard-coded, and one list keeps the terminal and the web from disagreeing. |

### Prior art

Sources: DeepSeek Harness's design notes in its repository (`.agents/notes/archived/architecture/`,
2026-07 to 2026-09); Claude Code's plugin documentation.

| | DeepSeek Harness (`dsh`) | Claude Code | Kollab, this spec |
|---|---|---|---|
| Unit | a Cordis plugin (npm package) in a profile | a folder with `.claude-plugin/plugin.json` | a folder with `kollab-plugin.json` |
| Web UI | the browser runs its own Cordis tree; each plugin's `client.js` loads into named slots (`sidebar.panellist`, `main`, `settings.plugin.item`); React is one shared module | the CLI has none; the desktop app's mods add panels beside the chat, a status line, toasts and slash commands | one ES module per plugin, into eight contribution points |
| Backend | everything is a plugin (model adapter, tools, loop, sandbox, storage) in one process tree | slash commands, agents, skills, shell hooks with JSON on stdin, MCP servers | `BasePlugin` in every agent, engine routes, supervised background processes and agent runs, plus the file-based pieces kollab already reads (hooks use the Claude Code format) |
| Install | `dsh plugin --profile web add <pkg>` through pnpm; a Plugins page in the web sidebar | `/plugin install <name>@<marketplace>`, where a marketplace is a git repository | `kollab plugin add <git-url>` or Settings → Plugins |
| Trust | in-process; manifest compatibility is not enforced | runs as the user | runs as the user; the sheet; kollab refuses undeclared agent and secret requests on its own routes |
| Reload | hot, per plugin | restart | page reload |

## Test plan

Red first; each test lands failing before its code:

- `tests/unit/plugins/test_manifest.py`: bad id, unknown key, path and symlink escape, kollab too old,
  missing `web/dist`, a bundle with its own React, vendored name clash; the hello plugin passes.
- `tests/unit/plugins/test_sources.py`: a folder or a source starting with `-` refused; `https`, `ssh` and
  scp forms accepted; `#release/1.2` parsed; a ref starting with `-` or with other characters refused;
  the ref resolves to a 40-character commit; confirm refuses a changed commit or manifest hash.
- `tests/unit/plugins/test_services.py`: the wrapper writes its pid file first; identity by pid, start
  time and nonce, with a stage child and another install's process never matching; restart backoff and
  the five-crash stop with no engine; exit codes kept; SIGTERM passed to the child, then SIGKILL after
  `stop_timeout_s`; log rename at 10 MB; the heartbeat label. The fake clock patches `time.monotonic`
  together with `time.sleep`.
- `tests/unit/plugins/test_plugin_entries.py`: built-in, loose, installed and dev plugins all listed;
  `get_config_widgets()` and `settings.json` give the same fields; `/config` lists a plugin installed
  outside `plugins/`; the Configuration panel carries no plugin sections.
- `tests/unit/plugins/test_settings.py`: every save path goes through the one writer; installed values
  land in `settings.json` and never touch `config.json`; `set_enabled` writes the right store per kind.
- `tests/unit/plugins/test_python_entries.py`: every enabled `python` entry loads with stdout and stderr
  captured, and any output fails the test; an entry that raises is skipped and reported.
- `tests/unit/plugins/test_cli_guard.py`: management commands refuse without a terminal; the agent file
  tools deny reading `engine.token`, `run/token` and files under `~/.kollab/plugins/`.
- `tests/unit/plugins/test_install_integrity.py`: a URL with credentials refused; an ambiguous ref
  refused; 64-digit commits accepted; a file changed after staging fails confirm; a file changed after
  install shows `Changed on Disk`; a failure part-way leaves the old version in force.
- `tests/unit/engine/test_plugin_routes.py`: `GET /plugins` schema; the dispatcher mounts and unmounts;
  401 without a token; the plugin token refused outside its id and on management routes; management
  routes refuse a non-local caller; `plugins-changed` sent after each management action; only allowed
  `web/dist/` files served, never HTML or dotfiles.
- `tests/unit/engine/test_plugin_agents.py`: POST answers at once and GET returns the result; defaults
  applied; refused `env` names; `runs.json` written atomically and re-read after a simulated restart;
  the daemon gate stops a run past its deadline or cap with no engine; the fifth concurrent run gets 429;
  the daily counter from runs and `usage.jsonl`, its 429 and its midnight reset; a usage line written
  after midnight counts toward the new day.
- `tests/unit/engine/test_plugin_policy.py`: absent `approval` means ask; secrets refused for a run with a
  shell or network tool; `PUT settings` and `@ctx.local_only` routes refused for remote callers through
  the proxy and the PhoneLink bridge; a token in a log line comes back redacted; a second wrapper exits
  while the first holds the lock.
- `packages/kollabor-webui/frontend/src/plugins/*.test.ts`: error boundary, Failed to Render, the 5 s
  limit, Retry with a new URL, a late import ignored, `plugins-changed` adds and removes rows, a chat on
  a removed tab goes to Chat, one React instance, text from GitHub rendered as text.
- Browser acceptance `frontend/tests/acceptance/plugins.browser.mjs`: the hello plugin adds a row and
  opens a page, a modal and a tab at 1440 and 390 px; turning it off removes them live; a throwing plugin
  shows Failed to Load.
- The kill, crash and restart tests of `test_services.py` also run under `docker run --rm`, and the agent
  environment tests seed `SSH_AUTH_SOCK`, `GH_*`, `GITHUB_*` and `ghp_`/`github_pat_` values and assert
  none reach a run.
- The pr-chain plugin keeps its own five test suites (drift, UI loop, loop, scenarios, UI stack) in its
  `tests/` folder and runs them with `python3 -B -m pytest tests/`.
- tmux spec `tests/tmux/specs/plugin-cli.json` in a throwaway HOME: `kollab plugin add` of the hello
  plugin's test repository shows the prompt, `kollab plugin list` shows it, `/hello` answers.

Live proof:

- Read-only first: a throwaway HOME on the test stack ports (7434 and 8081), the pr-chain plugin under
  `kollab plugin dev` with `services.loop.autostart` set to `false` in its scratch copy, a copy of the
  real data, GitHub reads through the normal login, and the plugin's write routes refusing while
  `PR_CHAIN_READ_ONLY=1` is set. Compare every route and the five tabs with the running board.
- The import: the copied `board-config.json` sets a thread count other than the default; after the first
  enable, `GET /plugins/pr-chain/api/config` and the plugin's `settings.json` both report it.
- Restart the web UI while a stub background process runs: it keeps running and is found again.
- One review stage through `POST /plugins/pr-chain/agents` on a local worktree copy shows under PR Chain
  in the sidebar and in Trajectory while it works.
- Pushing, labeling, commenting or merging through the plugin happens only in the cutover, with the
  owner's yes.

Sibling surfaces to sweep: the right-hand menu (desktop and the phone sheet), Settings tabs, the
composer palette, chat cards, NoticeBar, the left sidebar groups and the terminal's `/config`. UI
review: a vision reviewer runs `ui-slop-review` on 1440, 820 and 390 px screenshots in dark and light.

## Docs and changelog fragment

- `docs/features/plugins.md`: add, manage and write a plugin.
- `docs/reference/agent-guide/plugins-and-hooks.md`: package plugins, contribution points, background
  processes.
- `README.md`: one line in the feature list.
- `changes/1028.added.md` in the first PR that ships behavior: `- Plugins: add one from a git repository;
  it can add a right-menu row, a tab, modal or page in the web UI, background processes and agents
  (#1028).`

This spec file is docs only, so it carries no changelog fragment.

## Open questions

1. Where the pr-chain plugin repository lives (public or private) and under which account. Until it
   exists, build step 4 develops the plugin with `kollab plugin dev`, and the acceptance test runs that way.
2. Which kollab release first ships plugins. The manifest's `kollab` range needs it, and a release tag
   waits for the owner's word.
