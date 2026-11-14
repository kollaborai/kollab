# Plugins for the web UI and the backend

Issue: #1028  
Status: Approved (2026-11-14, by the owner, with mockups of every surface); review rounds in progress, see
`plugins.review.md`  
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

**Right-hand menu.** Each enabled plugin can add rows after Who Is Online, before Settings: an icon, a
Title Case label and a short count on the right, like MCP Servers' `2/5`. PR Chain shows `PR Chain  4`,
the chains running now. Hovering shows `4 chains running, 1 chain needs you`; the second part shows
only when something needs you, and both parts use the singular for 1. When kollab itself knows the
plugin is unwell, kollab sets the count: `Stopped` after the crash limit, `Failed to Load` when its code
failed. Plugin rows sort by plugin name. On a touch screen every row, tab and button is at least 44 px
tall. On a phone the menu is the existing right-hand sheet and the rows are there too.

**What a row opens.** The plugin picks one of three per row, or an external link:

- **Tab**: a main-area view beside Chat, Trajectory, Code Editor, Git and Terminal. It belongs to the
  open chat and receives its session and folder. The view list at the top of the right-hand menu shows
  it with the plugin's icon. A tab that needs a folder hides for a chat with none, like Code Editor.
- **Modal**: a dialog with the plugin's tabs, the same shape as Settings. On a phone it fills the screen.
- **Page**: the whole main area with the plugin's own tab bar. PR Chain opens a page with Board, Pull
  Requests, Issues, Spend and Config. Picking an agent in the left sidebar leaves the page.

On a phone a page or modal fills the screen and its tab bar scrolls sideways.

**Plugin agents.** An agent a plugin starts is a normal kollab session. While it runs it shows in the
left sidebar under a group named after the plugin (`PR Chain  2 Agent Runs`, or `1 Agent Run`), and Chat
and Trajectory show its work live. Run states read Done, Timed Out, Token Cap Reached, Failed or
Cancelled.

**Settings → Plugins.** A new Settings tab lists every plugin in two groups, Built In (Hub, Voice, MCP
and the rest of the bundled `plugins/`) and Installed: name, version, source, an on/off switch, its
Background Processes with their state, what the plugin uses, and its settings form. Buttons: Add Plugin,
and Update and Remove for installed plugins. Add Plugin takes a git URL. Plugin settings move here from
Settings → Configuration, which keeps only kollab's own settings, so each field shows in one place.
Installing, updating, removing and turning plugins on or off work only from a page opened on this
computer; over PhoneLink the tab is read-only and says `Manage plugins on this computer`.

Background process states: `Running · 2 h`; `Running · No Heartbeat for 6 min` (a process that
promised a heartbeat and went quiet); `Exited`; `Stopped` (stopped by you); `Stopped After 5 Crashes ·
View Log`. The list row shows the same text.

**Add sheet.** Shown before anything is copied or run:

```
Add PR Chain?
From            github.com/<owner>/kollab-pr-chain at 3f2a9c1 (main)
Adds            a right-menu row and a page · code that runs in every agent · engine routes
                1 background process · 2 slash commands · 6 agents · no hooks · no MCP servers
Through Kollab  runs agents with your loadouts
                agents that run tools without asking
                its own code reads the secret GITHUB_TOKEN; its agents never get it
On Its Own      runs gh, git, codex and claude; its coding agents use a shell without asking
                talks to github.com and raw.githubusercontent.com
Daily Token Cap 2,000,000 tokens a day: kollab agent runs plus the usage the plugin reports
Kollab refuses requests made through kollab that the plugin did not list. It cannot stop plugin code
from doing more on its own: plugins run as you, with your files and keys. Add only plugins you trust.
[Add Plugin]  [Cancel]
```

Every update shows the same sheet with the commit range and the folders that changed, and waits for a
click. Nothing updates by itself. `kollab plugin dev <folder>` shows the sheet with
`From  Dev Folder (not pinned)`.

**Turning a plugin off.** With agent runs working, Off asks first: `Turn off PR Chain? 2 agent runs are
working and will be cancelled.` (`1 agent run is working and will be cancelled.` for one) [Turn Off]
[Cancel]. Its background processes get their stop time to finish the current step. Every open page
updates at once: the row goes, a page that was open shows `PR Chain is off` with a Back to Chat button,
and a chat showing its tab switches to Chat.

**Notices.** A plugin can raise a NoticeBar notice: one line and one button, with no ids or version
numbers in the text: `A pull request is waiting for your review.  [Open]`.

**Composer and chat.** Plugin slash commands appear in the composer palette. A plugin can draw its own
card for a tool or message it owns, such as a PR status card.

**Failures.** A plugin whose code fails while loading keeps its row as `PR Chain  Failed to Load`, and
one that fails while drawing shows `Failed to Render`; both offer Retry and View Error, and everything
else keeps working. `?plugins=off` in the page URL loads the web UI with no plugin code and Settings →
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

`add`, `update`, `remove`, `enable`, `disable` and `dev` need a person: they refuse to run without a
terminal on stdin, and refuse inside an agent's shell. The `--purge` prompt: `Delete PR Chain's data
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
  settings.json           optional: the plugin's settings section
```

### Manifest: `kollab-plugin.json`

```json
{
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
      "heartbeat_s": 900
    }
  },
  "web": { "entry": "web/dist/index.js", "style": "web/dist/index.css" },
  "agents": "agents", "skills": "skills", "commands": "commands",
  "hooks": "hooks/hooks.json", "mcp": ".mcp.json",
  "settings": "settings.json",
  "uses": ["agents", "agents:unattended", "secret:GITHUB_TOKEN", "run:gh,git,codex,claude",
           "net:github.com,raw.githubusercontent.com"]
}
```

| Key | Meaning |
|---|---|
| `id` | `^[a-z0-9][a-z0-9-]{1,39}$`; the install folder and data folder name |
| `name` | Title Case: menu row, sidebar group, Settings |
| `kollab` | version range; `add` refuses a kollab outside it |
| `python` | a `BasePlugin` class that loads in every agent daemon |
| `engine` | `make_router(ctx: PluginContext) -> APIRouter`, mounted under `/plugins/{id}/api/` |
| `services.<name>` | `command`; `autostart`; `restart` (`on-failure` default, `always`, `never`); `stop_timeout_s` (default 30); `heartbeat_s` (optional) |
| `web` | the entry module and stylesheet, both inside `web/dist/` |
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

A request through kollab without its entry fails with 403 naming the missing entry (`pr-chain did not
declare agents:unattended`). That refusal covers only requests that go through kollab's routes and
`ctx`. Plugin code runs as the user, so it could go around kollab; the add sheet says so. When a plugin
runs coding agents of its own (pr-chain's `codex exec` and `claude -p` stages), the sheet says what they
can do under On Its Own.

### Where plugins come from

`add` takes a git repository: an `https://` or `ssh://` URL, or scp-style `git@host:owner/repo`, with an
optional `#<ref>` (`https://github.com/o/r#release/1.2`). Local folders are not installed; they run with
`kollab plugin dev`, unpinned and labeled. A ref must match `^[A-Za-z0-9][A-Za-z0-9._/-]*$`. Kollab runs
`git ls-remote -- <url> <ref>`, `git clone -- <url> <staging>` and `git -C <staging> checkout --detach
<sha>`, so nothing a user typed reaches git as an option. Before the sheet, the ref resolves to a full
40-character commit; the sheet, `plugins.json` and `update` use that commit, and an update follows the
ref recorded at add.

### Where plugins live

| Path | Holds | Written by |
|---|---|---|
| `~/.kollab/plugins/<id>/` | the installed plugin, a clean copy at the pinned commit | `add`, `update` |
| `~/.kollab/plugins.json` | `{"plugins": {"<id>": {"source", "ref", "commit", "manifest_sha256", "enabled", "granted": [...], "added", "updated"}}, "dev": {"<id>": "<folder>"}}` | kollab, under `kollabor_config.file_lock` |
| `~/.kollab/plugin-data/<id>/` | the plugin's own state (`KOLLAB_PLUGIN_DATA`); kept on update and remove | the plugin |
| `~/.kollab/plugin-data/<id>/settings.json` | the plugin's settings values: the only store for them | kollab (see Settings) |
| `~/.kollab/plugin-data/<id>/run/` | `token` (0600), `<process>.pid`, `<process>.state`, `<process>.log`, `runs.json`, `usage.jsonl`, `usage-<date>.json` | kollab, and `usage.jsonl` appends from the plugin |
| `~/.kollab/plugin-data/<id>@dev/` | a dev run's own data folder | the dev run |

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
| `ANY /plugins/{id}/api/{path}` | the plugin's router, through one dispatcher to a per-plugin sub-app, so enable and disable need no engine restart | own id | yes |
| `GET`, `POST /plugins/{id}/events` | the plugin's SSE stream of `{topic, data, ts}`; the engine sets `ts` | own id | yes |
| `GET`, `PUT /plugins/{id}/settings` | read and save its settings (Settings below) | own id | yes |
| `POST /plugins/{id}/agents` → `{run_id, session_id}` at once; `GET`, `DELETE .../agents/{run_id}` | agent runs | own id | yes |
| `POST /plugins/{id}/services/{name}/start`, `/stop`, `/restart`; `GET .../log?tail=200` | background processes | own id | yes |
| `POST /plugins/install`, `POST /plugins/{id}/update` → `{job_id}`; `GET /plugins/jobs/{job_id}` | clone or fetch into staging, check, then report the sheet facts | no | no |
| `POST /plugins/jobs/{job_id}/confirm` | `{commit, manifest_sha256}`: refused unless both match the staged copy; then move into place | no | no |
| `POST /plugins/{id}/enable`, `/disable`, `/remove` | manage | no | no |

"Plugin token: no" means only the user's engine token works. "Remote page: no" means the route refuses
any caller that is not on this computer, the way the PhoneLink routes already check, so a page opened
over PhoneLink or the relay can use plugins but never install or manage them.

### Tokens and what plugin code can reach

- The page's `/config` endpoint hands the user's full engine token to every script on the page, so web
  plugin code holds the user's full engine access, including the management routes from a local page.
  Plugin code on disk can read `~/.kollab/engine.token` too. Decision D1 accepts this; the add sheet says
  plugins run as the user.
- The plugin token (`KOLLAB_PLUGIN_TOKEN`) is minted once per plugin, kept in `run/token`, and given to
  background processes and the engine router. The engine accepts it only on its own id's routes marked
  above. It narrows mistakes and attributes calls; it is not a boundary against plugin code.
- Agent runs are a boundary kollab does hold: `~/.kollab/engine.token` and every `plugin-data/*/run/token`
  join the agent file tools' protected paths (`is_protected_path`,
  `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py:271`), and management commands
  refuse inside an agent's shell, so an agent steered by text it reads cannot install or enable a plugin.

### Serving web files

The web UI server serves `GET /plugins/{id}/web/{path}` only from the plugin's `web/dist/` folder, only
files ending in `.js`, `.mjs`, `.css`, `.png`, `.svg`, `.woff2` or `.json`, never a dotfile, and with
`X-Content-Type-Options: nosniff`. An HTML file is never served, so nothing in a plugin folder can open
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
`KOLLAB_ENGINE_URL` and `KOLLAB_PLUGIN_TOKEN`, and read settings from `<data>/settings.json`.

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
- `folder` goes through `folder_or_400`. `approval: "trust_all"` needs `agents:unattended`; each name in
  `secrets` needs `secret:<NAME>`.
- Environment: the user's environment with secret-looking names removed (the filter the Code terminal
  uses, #987), then `env`, then each granted secret. `env` cannot set secret-looking names, `HOME`,
  `PATH`, `SSH_AUTH_SOCK`, or names starting `KOLLAB_`, `PYTHON`, `LD_` or `DYLD_`.
- The engine starts the daemon through `DaemonPool` with `KOLLAB_USAGE_LANE=plugin:<id>`,
  `KOLLAB_USAGE_PR=<labels.pr>` (usage ledger, #723), `KOLLAB_RUN_DEADLINE` and `KOLLAB_RUN_MAX_TOKENS`.
  The daemon refuses provider requests past either limit, at the same dispatch-time gate `/goal` budgets
  use (`kollabor/goals/`), so the limits hold while the engine is down. The engine sends the brief and
  stops the daemon at `turn_complete`; the conversation stays on disk.
- Each run is a row in `run/runs.json` (run id, session id, daemon pid and start time, deadline, cap,
  status, tokens so far, and the result when it ends), rewritten whole with a temp file and rename under
  `ctx.lock`. Finished rows stay for the daily count and the result read.
- A plugin runs at most 4 agents at once (`max_concurrent_runs`); the next start gets 429.
- `tools` goes through the agent bundle's tools allowlist. That allowlist has open gaps (#889 to #897),
  so agent runs ship only after those issues close.

### Usage and the daily cap

The engine keeps one counter per plugin per day in `run/usage-<date>.json` (local date): it adds every
finished agent run's usage, and every line a plugin appends to `run/usage.jsonl`
(`{ts, model, input, output, labels}`; appending works with or without an engine, and the engine reads
new lines on its next tick). `max_tokens_per_day` (default 2,000,000) refuses the next agent run with 429
once the counter reaches it, and resets at 00:00 local time. Tokens a plugin spends without appending
them are not counted; pr-chain appends one line per command stage from the transcript it already reads.

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
  k.menu({
    id: "pr-chain",
    label: "PR Chain",
    icon: "git-pull-request",
    count: { path: "/badge", refreshOn: "chain" },
    open: { page: "board" },
  });
});
```

Every `render` is a React component that receives `{ k, session }` as props (`session` only inside a
`view`). The `/badge` route answers `{text, hint}`.

Contribution points. Each is one existing component that also reads the plugin registry:

| Call | Shows in | Existing owner |
|---|---|---|
| `k.menu({id, label, icon, count?, open})` | a right-hand menu row | menu rows in `SessionPanel.tsx` |
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
rebuilt `web/dist/`.

### Settings

A plugin's settings are one section in the shape `get_config_widgets()` already returns:
`{"title": "PR Chain", "widgets": [{"type", "label", "config_path", "help", ...}]}`, where `config_path`
is relative to the plugin. `settings.json` holds exactly that section, so a package plugin and a
`BasePlugin` declare settings the same way.

```json
{
  "title": "PR Chain",
  "widgets": [
    { "type": "spinbox", "label": "Max Threads", "config_path": "loop.threads", "help": "Chains that run at once.",
      "min_value": 1, "max_value": 4, "step": 1, "default": 1 },
    { "type": "checkbox", "label": "Build Issues Into PRs", "config_path": "issues.build", "default": true }
  ]
}
```

Values have one writer, `kollabor_plugins.settings.save(id, values)`, which checks each value against
its widget and writes the plugin's store with a temp file and rename under `kollabor_config.file_lock`.
`PUT /plugins/{id}/settings`, `k.settings.save`, `ctx.settings.save`, Settings → Plugins and the
terminal's `/config` all call it.

- An installed plugin's store is `~/.kollab/plugin-data/<id>/settings.json`, never
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
PluginEntry = {id, name, version, kind: "built-in" | "installed" | "dev", source, commit, enabled,
               state: "ok" | "error" | "off", error, settings: [panel fields], web: {entry, style, hash} | null,
               contributes, services: [{name, state, pid, since, restarts, heartbeat}], uses, granted}
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
  (`{pid, start_time, nonce}`). The process counts as running only when that pid is alive, its start time
  matches, and it is the wrapper (its command line carries the nonce). Nothing is found by scanning other
  processes, so a stage child or a test install can never be mistaken for the service. Otherwise the
  supervisor starts a wrapper with a fresh nonce, and the wrapper writes the pid file before it starts the
  command.
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
Update stops processes the same way, swaps the folder, and starts them again.

### pr-chain as a plugin

The plugin lives in its own repository and installs with `kollab plugin add <git-url>`. Until that
repository exists, step 4 develops it with `kollab plugin dev`.

| pr-chain today | As a plugin |
|---|---|
| the `pr-chain` command, its prompts, `ui_stack.py`, `ui_shoot.py` and the shared helpers it calls (`fold.py`, `land_pr.py`) from outside its folder | all inside the plugin: `bin/`, `prompts/` and `python/`; every path resolves from `KOLLAB_PLUGIN_ROOT` or `KOLLAB_PLUGIN_DATA`, never the home folder |
| the loop kept alive with `nohup` | background process `loop`; it gains a SIGTERM handler that starts no new lap, passes SIGTERM to its chains and exits when they stop; `stop_timeout_s` 2400 covers one stage |
| `board-config.json` (thread cap, issue building) | plugin settings `loop.threads` and `issues.build`; the Config tab and Settings → Plugins edit the same values |
| `models.json`, `profiles.json`, `commands.json` | files in the data folder, edited by the Config tab through the plugin's routes |
| `rates.json` and the downloaded price table | shipped in the plugin; a refresh fetches from `raw.githubusercontent.com`, declared in `uses` |
| `ledger.jsonl`, `prs/<n>/STATE.json`, `issues/<n>/`, locks, claims, logs, the `STOP` file | the same files under `~/.kollab/plugin-data/pr-chain/` |
| the board's own web server and port | an `engine` router and a `web` page with the same five tabs |
| board JSON routes `/api/state`, `/api/prs`, `/api/pr/<n>` (and `/diff`), `/api/issues`, `/api/issue/<n>`, `/api/spend`, `/api/config` (GET, POST) | the same handlers at `/plugins/pr-chain/api/<same path>` with the same query parameters (`?pr=`, `?state=`), plus `/badge` for the menu row |
| the Board's 3D stage map (three.js) | the same stage map, using `k.ui.GemAvatar` or a three.js copy bundled in `web/dist/` |
| Agent Output from the bigger of each stage's `err.txt` and `out.jsonl`, polled every 3 s | the same, passed through one redaction function before it is served: token URLs, `Authorization` values, keys and the engine token are replaced with `[redacted]` |
| stage agents as command lines (`codex exec`, `claude -p`) with every tool and no approval prompts | the same, said plainly on the add sheet; a stage can instead name a kollab loadout, which runs through `agents.run` with a read-only tool list for review, audit and UI stages and shows in the sidebar |
| spend from transcripts and a price table | unchanged, plus one line per command stage appended to `run/usage.jsonl` for the daily cap |
| a relay process to reach the board from other devices | not needed: the web UI already serves remote devices with auth |
| `routes/pr_chain.py`, `pr_chain.py` and `components/pr-chain/` in kollab (#1008) | removed from core once the plugin page has parity |

Command stages keep pr-chain's guard: each runs with a scratch HOME (no gh or ssh config), no
`GH_TOKEN`, `GITHUB_TOKEN` or `SSH_AUTH_SOCK`, and an empty `GH_CONFIG_DIR`. Only the plugin's own code
pushes, labels, comments and merges. Kollab does not sandbox these stages.

Parity means that for the same data every route above answers what the current board answers, and
screenshots of the five tabs match at 1440, 820 and 390 px wide in dark and light.

### Cutover from the running tool

Two loops never run on the same PRs. With the owner's yes:

1. `pr-chain stop` writes the `STOP` file; the running chains finish their stage and the loop exits.
2. Kollab imports once into `~/.kollab/plugin-data/pr-chain/`: the state folder, `models.json`,
   `profiles.json` and `commands.json`, and the thread cap and issue switch from `board-config.json` into
   the plugin's settings. Locks and claims that hold only a pid are dropped. A marker file records the
   import, so it never runs twice.
3. The old board stays up read-only on the old copy while the plugin page is compared with it.
4. With parity shown, the plugin's loop is turned on and the old loop, board and relay are retired.

### Owning files

| File | Change |
|---|---|
| `packages/kollabor-plugins/src/kollabor_plugins/packages.py` | new: source checks, manifest checks, add, update, remove, `plugins.json` |
| `packages/kollabor-plugins/src/kollabor_plugins/services.py` | new: supervisor and the `run-service` wrapper |
| `packages/kollabor-plugins/src/kollabor_plugins/settings.py` | new: the one settings writer and `set_enabled` |
| `packages/kollabor-plugins/src/kollabor_plugins/context.py` | new: `PluginContext` |
| `packages/kollabor-plugins/src/kollabor_plugins/registry.py`, `discovery.py`, `plugin_sdk.py` | `plugin_entries()`; packages load through their manifest; loose modules as today |
| `packages/kollabor-tui/src/kollabor_tui/config_widgets.py` | drop the scanner; build the Plugins part from `plugin_entries()` |
| `kollabor/panels/config.py` | stop adding plugin sections to Settings → Configuration |
| `packages/kollabor-config/src/kollabor_config/plugin_config_manager.py`, `loader.py` | fold into `plugin_entries()` |
| `kollabor/application.py`, `kollabor/cli.py` | load package `python` entries; `kollab plugin ...`, `run-service`, `--no-plugins` |
| `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py` | protect `engine.token` and plugin `run/token` files |
| `kollabor/goals/` | honor `KOLLAB_RUN_DEADLINE` and `KOLLAB_RUN_MAX_TOKENS` at the dispatch gate |
| `packages/kollabor-engine/src/kollabor_engine/routes/plugins.py`, `plugin_agents.py`, `server.py` | routes, agent runs, usage counter, supervisor tick |
| `packages/kollabor-webui/src/kollabor_webui/server.py` | serve `/plugins/{id}/web/*` from `web/dist/` only |
| `packages/kollabor-webui/frontend/src/plugins/` | new: loader, registry, `sdk.ts`, error boundary |
| `packages/kollabor-webui/frontend/vite.config.ts`, `index.html` | `plugin-runtime` entry and import map |
| `SessionPanel.tsx`, `App.tsx`, `panel-model.ts`, `PanelHost.tsx`, `Thread.tsx`, `NoticeBar.tsx`, `AppSidebar.tsx` | read the registry |
| `bundles/plugins/hello/` | new: the smallest plugin using every part; test fixture and `kollab plugin new` template |

### Build order

1. Packages, the wrapper and supervisor, the one plugin list with `/config` reading it, the settings
   writer and the CLI, with the hello plugin. Terminal only.
2. Engine routes except agent runs: `/plugins`, events, settings, background processes, install and
   management.
3. Web loader, registry and the eight contribution points; Settings → Plugins; the add sheet.
4. Agent runs, once #889 to #897 are closed. The pr-chain plugin, developed with `kollab plugin dev`.
5. Cutover, then removal of the #1008 reader and dialog.

Steps 2 and 3 share no files and can run at the same time once step 1 lands.

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
15. If an agent's shell runs `kollab plugin add` or reads `~/.kollab/engine.token`, then the command
    refuses and the read is denied, so text an agent reads cannot install or enable a plugin.
16. If a PR title holds `"><img src=x onerror=alert(1)>`, then every plugin view shows it as plain text.

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
   instructions. Kollab agent runs default to `approval: "ask"`, cannot read token files, and cannot run
   plugin management; unattended runs need `agents:unattended`, which the sheet shows. pr-chain's command
   stages keep its own guard (no GitHub credentials, scratch HOME), and only its own code writes to
   GitHub.
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
| D10 | Every update asks and confirms the exact commit; management needs a person on this computer; agent runs cannot read token files. | maintainers, owner-approved | Changed code that already holds a granted secret is a new trust decision, and text an agent reads must not be able to install code. |
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
- `tests/unit/plugins/test_cli_guard.py`: management commands refuse without a terminal and inside an
  agent's shell; the agent file tools deny `engine.token` and `run/token`.
- `tests/unit/engine/test_plugin_routes.py`: `GET /plugins` schema; the dispatcher mounts and unmounts;
  401 without a token; the plugin token refused outside its id and on management routes; management
  routes refuse a non-local caller; `plugins-changed` sent after each management action; only allowed
  `web/dist/` files served, never HTML or dotfiles.
- `tests/unit/engine/test_plugin_agents.py`: POST answers at once and GET returns the result; defaults
  applied; refused `env` names; `runs.json` written atomically and re-read after a simulated restart;
  the daemon gate stops a run past its deadline or cap with no engine; the fifth concurrent run gets 429;
  the daily counter from runs and `usage.jsonl`, its 429 and its midnight reset.
- `packages/kollabor-webui/frontend/src/plugins/*.test.ts`: error boundary, Failed to Render, the 5 s
  limit, Retry with a new URL, a late import ignored, `plugins-changed` adds and removes rows, a chat on
  a removed tab goes to Chat, one React instance, text from GitHub rendered as text.
- Browser acceptance `frontend/tests/acceptance/plugins.browser.mjs`: the hello plugin adds a row and
  opens a page, a modal and a tab at 1440 and 390 px; turning it off removes them live; a throwing plugin
  shows Failed to Load.
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
   exists, build step 4 develops the plugin with `kollab plugin dev`.
