---
title: "Apps: one place to add MCP servers, CLI tools and skills"
created: 2026-10-08
modified: 2026-10-08
status: building
author: maintainers
---

# Apps

## objective

An **Apps** button in the web UI sidebar opens one directory of things an agent
can use: MCP servers, CLI tools and skills. Each row has an icon, a name, a
one-line description and an **Add** button (or **✓ Added**). Adding does the
real work: an MCP server lands in the MCP config and connects to the session, a
CLI tool is installed with a package manager already on the machine, a skill is
downloaded into the skill library. `/apps` is the same directory in the
terminal.

Reference look: a dark "Marketplace" modal (title + "16 installed >" with
stacked icons, a full-width rounded search bar, sections "For You",
"Featured" with "View all", two-column rows). Kollab's version is titled
**Apps** so the button and the modal share one name.

## files

| file | owns |
|------|------|
| `bundles/data/apps.json` | the catalog (schema below) |
| `scripts/validate_apps.py` | catalog validator; `--online` also checks every package/repo exists |
| `kollabor/apps/catalog.py` | load + validate the catalog (bundled file, then `~/.kollab/apps.json` merged on top by `id`+`kind`) |
| `kollabor/apps/service.py` | row state, add/remove/enable, installers |
| `kollabor/apps/registry.py` | MCP Registry search, ranking, and a registry server as an MCP entry |
| `kollabor/panels/apps.py` | `apps` (picker), `apps-registry` (picker) and `apps-install` (form) panels |
| `kollabor/commands/...` | `/apps` slash command (terminal text mode) |
| `packages/kollabor-agent/src/kollabor_agent/mcp_manager.py` | `upsert_server(name, definition)` / `remove_server(name)` (the one place that writes `mcp_settings.json`) |
| `packages/kollabor-webui/frontend/src/components/apps/AppsDialog.tsx` | the modal |
| `packages/kollabor-webui/frontend/src/components/shell/AppSidebar.tsx` | the **Apps** button |

## catalog schema (`bundles/data/apps.json`)

```jsonc
{
  "schema_version": 1,
  "updated": "2026-10-08",
  "recommended": ["mcp:context7", "mcp:playwright", "cli:gh", "skill:pdf"],
  "featured":    ["mcp:github", "mcp:linear", "mcp:notion", "mcp:sentry", "..."],
  "apps": [ App, ... ]
}
```

`recommended` and `featured` hold **row ids** (`<kind>:<id>`), in display order.

### App

| field | type | rule |
|-------|------|------|
| `id` | string | `^[a-z0-9][a-z0-9-]{0,47}$`, unique **per kind**. It is the MCP server name, the CLI's catalog id, the skill folder name |
| `kind` | `"mcp"` \| `"cli"` \| `"skill"` | |
| `name` | string | display name in the vendor's own casing: "GitHub", "Google Drive", "ripgrep" |
| `description` | string | one sentence, ≤ 90 chars, says what the agent can do with it |
| `publisher` | string | who maintains it: "GitHub", "Anthropic", "Model Context Protocol" |
| `homepage` | https URL | repo or docs page |
| `official` | bool | published by the vendor of the service itself |
| `tags` | string[] | optional, lowercase search words |
| `icon` | object | optional `{ "path": "<svg path d, 24x24 viewBox>", "hex": "181717" }` (Simple Icons data). Absent: a letter tile |
| `requires` | string[] | optional executables that must be on PATH: `["npx"]`, `["uvx"]`, `["docker"]`; an MCP row is unavailable until they are, a skill's Add installs a missing one first from the catalog CLI entry whose `bin` it is |
| `mcp` | object | kind `mcp` only, see below |
| `cli` | object | kind `cli` only, see below |
| `skill` | object | kind `skill` only, see below |

### `mcp`

```jsonc
"mcp": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "{ALLOWED_DIR}"],
  "inputs": [
    { "name": "ALLOWED_DIR", "label": "Folder", "to": "arg", "required": true, "default": "{workspace}" },
    { "name": "GITHUB_PERSONAL_ACCESS_TOKEN", "label": "Personal Access Token", "to": "env",
      "secret": true, "required": true, "help": "github.com/settings/tokens, repo scope" }
  ]
}
```

- Transport is stdio only (the MCP runtime's v1 limit). A hosted server goes
  through the `mcp-remote` bridge: `"command": "npx", "args": ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]`.
- `inputs[].to` is `"env"` (default) or `"arg"`. An arg input replaces the
  `{NAME}` token in `args`. Built-in tokens: `{workspace}` (session workspace),
  `{home}`.
- Adding writes `{"type": "stdio", "command", "args", "env", "enabled": true,
  "description"}` under `servers.<id>` of `~/.kollab/mcp/mcp_settings.json` —
  the shape `mcp_integration.py` already reads.

### `cli`

```jsonc
"cli": {
  "bin": "gh",
  "install": [
    { "manager": "brew", "package": "gh" },
    { "manager": "npm",  "package": "..." }
  ],
  "post_install": "gh auth login"
}
```

- `manager` ∈ `brew` (`brew install <package>`; `"cask": true` adds `--cask`),
  `npm` (`npm install -g <package>`), `uv` (`uv tool install <package>`),
  `pipx` (`pipx install <package>`). These run without sudo. System package
  managers are not in v1.
- Add runs the first method whose manager is on PATH. argv list, never a shell.
  `package` must match `^[A-Za-z0-9@/._+-]+$`.
- Installed = `shutil.which(bin)`. `post_install` is shown after a successful
  add (interactive logins run in the user's terminal, never from the daemon).

### `skill`

```jsonc
"skill": { "repo": "anthropics/skills", "path": "skills/pdf", "ref": "main" }
```

- Add streams `https://codeload.github.com/<repo>/tar.gz/<ref>` (the whole repo,
  up to 300 MB, spooled to disk past 8 MB) and extracts
  `path/` into `~/.kollab/skills/<id>/`, and writes `.kollab-app.json`
  (`{id, repo, path, ref, installed_at}`) next to `SKILL.md`.
- The extracted `SKILL.md` frontmatter `name` must equal `id` (Agent Skills
  rule). Reject archive members that are absolute, contain `..`, or are links;
  cap the extracted size at 5 MB.
- Skills already in the library (bundled, `~/.kollab/skills`,
  `.kollab/skills`) are rows too, without a catalog entry: bundled ones carry
  the badge **Built-in**, user-written ones **Local**. Neither can be removed
  from Apps. Remove deletes only a folder that has `.kollab-app.json`.

## row state

| kind | `added` | `off` | `available` | `unavailable` |
|------|---------|-------|-------------|---------------|
| mcp | configured and enabled | configured, disabled | not configured | a `requires` binary is missing |
| cli | `which(bin)` finds it | — | not found, a manager is on PATH | no listed manager on PATH |
| skill | in the library | — | catalog entry, not in the library (note "Installs X first" when Add installs a `requires` tool) | a `requires` binary is missing and no catalog CLI entry can install it |

An MCP row's `note` adds the session's runtime fact when known
("connected · 12 tools", "error: …"). Configured, enabled, connected and
tool count stay separate facts (`mcp-server-management.md` invariant).

## panels (wire format of `webui-unified-config.md`)

### `apps` (picker)

```jsonc
{
  "panel": "apps", "kind": "picker", "title": "Apps",
  "summary": [{ "label": "Installed", "value": "16" }],
  "sections": [
    { "id": "for-you",  "title": "For You",     "ids": ["..."] },
    { "id": "featured", "title": "Featured",    "ids": ["..."] },
    { "id": "mcp",      "title": "MCP Servers", "ids": ["..."] },
    { "id": "cli",      "title": "CLI Tools",   "ids": ["..."] },
    { "id": "skill",    "title": "Skills",      "ids": ["..."] }
  ],
  "rows": [{
    "id": "mcp:github", "label": "GitHub", "detail": "Issues, pull requests and code search on GitHub.",
    "group": "mcp", "kind": "mcp", "publisher": "GitHub", "homepage": "https://...",
    "icon": { "path": "...", "hex": "181717" }, "tags": ["git"],
    "state": "available", "note": "", "badges": ["MCP"],
    "preview": "npx -y @modelcontextprotocol/server-github",
    "actions": ["add"]
  }],
  "row_actions": [
    { "id": "add", "label": "Add" },
    { "id": "enable", "label": "Turn On" },
    { "id": "remove", "label": "Remove", "confirm": true }
  ],
  "toolbar_actions": [{ "id": "refresh", "label": "Refresh" }]
}
```

- `For You` = `recommended` rows that are not `added` and not `unavailable`, at
  most 4. Empty: the section is left out.
- `Featured` = `featured` in catalog order. Kind sections list every row of that
  kind, `added` first, then by name.
- `badges`: `MCP` / `CLI` / `Skill`, plus `Built-in` or `Local` for library
  skills.
- `preview` is the exact command Add will run (secrets never shown).

### actions (`act`)

| action | payload | does |
|--------|---------|------|
| `add` | `{id}` | mcp with a missing required input → `open` = `apps-install` form. Otherwise installs, applies MCP changes to the session, returns `ok_result(message, panel=<fresh describe>)` |
| `remove` | `{id}` | mcp that Apps added (entry has `"added_by": "apps"`) → `remove_server`, then session reload; marketplace skill → delete its folder; cli, or an mcp entry set up by hand → 404 |
| `enable` | `{id}` | mcp `off` → `enable_server` + reload |
| `refresh` | `{}` | fresh describe (rescans PATH and the library) |

### `apps-install` (form)

`describe(ctx, {"id": "mcp:github"})` → one section with one `make_field` per
input (secret inputs are secret fields). Action `install` with
`{"changes": {NAME: value}, "context": {"id": "mcp:github"}}` writes the server
and returns `ok_result(..., panel=<apps describe>)`. A field sent as `""` stays
empty; only a field left out falls back to its default. For a registry server
the form also has a **Command** section first and its context carries the
fingerprint (see MCP Registry).

## terminal: `/apps`

Text mode, works in pipe mode and over attach:

- `/apps` — sections and rows: `[added] GitHub (mcp:github) — Issues, pull…`
- `/apps search <words>`
- `/apps add <row id or id>` — an MCP entry with a required secret is added
  **off** with an empty key and the reply says how to set it (`/mcp`); secrets
  never travel in a slash command.
- `/apps remove <row id or id>`

`COMMAND_PANELS["apps"] = "apps"`; the web opens the Apps modal for it, not a
Settings tab. `PANEL_LOCATIONS["apps"] = "Apps (sidebar)"`.

## web UI

- Sidebar button **Apps** (lucide `LayoutGrid`) right above **Settings**.
- Modal: `max-w-5xl`, dark surface, close X top right. Header: "Apps" left;
  right: up to 4 stacked icons of added rows + "N installed" + chevron →
  toggles an Installed-only view. Full-width rounded-full search
  ("Search apps") filters label, detail, publisher and tags.
- Sections in `sections` order; title left, "View all" right when a section has
  more than 4 rows (For You/Featured) or 6 rows (kind sections).
- Row: 48px rounded-xl icon tile (white tile with the brand path in `#hex`, or
  a muted letter tile), name + badge pill, one-line truncated description,
  right side **Add** (rounded-full secondary button) / **✓ Added** (muted text
  with a check; hover shows **Remove**) / **Turn On** / disabled **Add** with
  the note as tooltip. Two columns ≥ 768px, one column below.
- Add with inputs renders the returned `apps-install` form inside the modal
  with the existing `FormPanel`, with a Back button.
- 390px and 820px: no horizontal scroll, names and buttons never overlap.

## MCP Registry

The catalog stays curated. A search of two or more letters also asks the
official registry (`https://registry.modelcontextprotocol.io/v0`, about 41,000
latest servers, too many to sync).

- **Query.** `GET /servers?limit=100&version=latest&search=<s>` matches a
  substring of the whole name, so "github" alone hits every `io.github.*`
  namespace. Each search asks three ways at once: the longest word `w`, `w/`
  (a vendor's own namespace) and `/w` (names that start with it). Only
  `active` servers count, each once; every word must appear in the name, title
  or description.
- **Rank.** Namespace's last label equals `w`, then name or title equals it,
  starts with it, contains it, the rest; ties: has an https repository, shorter
  name, alphabetical. 20 rows show; the note says how many more.
- **Slow registry.** Measured 2026-10-08: 0.2-15 s per call, some stall past
  40 s. A try with no answer after 10 s gets one more beside it; the first
  answer wins, a 4xx is final, 30 s caps the call (`_get_cached`). The broad
  `w` call gets one try and 2 s after the other two, then finishes into the
  cache. Calls run on their own 32-thread pool; answers are cached 10 minutes.
  `RemoteStateService.PANEL_DESCRIBE_TIMEOUTS` gives `apps-registry` and
  `apps-install` 45 s.
- **Dedupe.** A server whose package or remote URL (version stripped) is a
  catalog entry's arg is left out; so is one already saved (its saved row
  lists it).
- **As an MCP entry** (`to_app`). A hosted server comes first:
  `npx -y mcp-remote <url> --header 'Name:${ENV}'`, so only mcp-remote runs
  locally. Packages: npm `npx -y id@ver`, pypi `uvx id@ver`, oci
  `docker run -i --rm -e NAME… image:tag`. The first way whose runner is on
  PATH wins. Secrets go only to env; header variables become env inputs that
  mcp-remote reads.
- **Refused, with the reason on the row:** a secret on a command line; an
  undeclared `{token}`; any `${…}` in a header that is not the server's own
  input (mcp-remote fills it from this computer's environment); `{workspace}`,
  `{home}` or `$` in a hosted server's address or headers; a registry default
  holding braces is dropped; an env variable a runner reads itself
  (`PATH`, `HOME`, `NODE_*`, `NPM_*`, `UV_*`, `PIP_*`, `PYTHON*`, `DOCKER_*`,
  `LD_*`, `DYLD_*`, `XDG_*`, proxies, CA bundles…); a runner flag outside
  `_RUNTIME_FLAGS` (npx: `-y`; uvx: none; docker: `-e`, `-w`, `--platform`,
  `--pull`, `-i`, `--rm`, `--init`, `-t`), and docker `-e NAME` for anything but
  the server's own variable; non-https addresses; package names outside `_REF`.
- **Empty inputs.** An optional input left empty is not saved, and Add drops
  what would pass it: its docker `-e NAME` pair (docker would copy the host's
  value), its mcp-remote `--header`, its optional argument (`inp["drop"]`).
- **Saved.** Under `candidate_ids(name)`: the segment after the slash (generic
  ones become `vendor-segment`), then with the vendor, then the namespace;
  catalog MCP ids stay reserved. The entry gets `registry`, `title`,
  `homepage`, `version` and `added_by: "apps"`. A catalog id later taken by a
  registry server shows the catalog row `unavailable`; the registry row stays.
- **Confirm, then Add.** Add on a registry row always opens `apps-install`:
  a **Command** section with the exact command (**Runs**), then the settings.
  The form context carries `fingerprint(app)` (sha256 of name, command, args
  and inputs, 16 hex). `AppsService.add(id, values, confirm)` loads the server
  again and saves only when the fingerprint matches; no code, no add. The
  terminal shows the command and the code with `/apps add registry:<name>` and
  adds with `/apps add registry:<name> <code>`.
- **Panels.** `apps-registry` (picker) describes `{query}` into one section of
  rows (`id` `registry:<name>`, `server` the saved key, badges `["Registry"]`)
  plus `note`; its row actions go through `apps`.
- **Text.** Registry text is one line with control and format characters
  removed; only https links survive.

## security

- Commands come from the catalog, or from a registry entry through
  `registry.to_app` and its refusals (MCP Registry above), shown before Add.
  User input reaches only `env` values and `{NAME}` arg tokens, never
  `command`, `manager` or `package`.
- Never log a payload. Secret values are redacted in every reply and log.
- Skill archives: GitHub `codeload` only, member checks above, 5 MB cap.
- CLI installs run with a 600 s timeout (`ponytail:` blocking call; upgrade
  path is a background job + polling if installs get slow).

## v1 exclusions

Per-agent enablement (everything is global), removing CLI tools, system package
managers, native HTTP/SSE MCP transports (hosted registry servers go through
mcp-remote), a remote catalog feed.

## deviations

Backend part 1 (catalog, validator, MCP writes, service). Each one is a place
the code had to decide something the table above leaves open.

- **One writer, private file.** `McpManager.save_config` now goes through the
  same atomic writer as `upsert_server` / `remove_server`. The file is written
  mode 0600 (`mkstemp`) because it holds API keys.
- **Broken settings file.** `upsert_server` and `remove_server` raise `ValueError`
  on invalid JSON. Reading through `load_config` would return `None` and the
  write would replace the user's file.
- **Validator reuse.** `upsert_server` runs `MCPIntegration._validate_server_config`
  on an uninitialized instance; the method reads no instance state and
  `mcp_integration.py` was outside this part's files. Make it a module function
  when that file is next open.
- **Missing inputs.** `add` on an MCP with a missing required secret writes the
  entry `enabled: false` with an empty value and names the secret in the reply
  (the terminal rule). A missing required non-secret input returns `ok: false` and
  writes nothing. The web panel calls `missing_inputs` first and opens the form.
- **Turn On** refuses while a secret env value is still empty, so an unset key
  cannot start a server.
- **Unavailable rows** carry `actions: []`. The UI draws the disabled Add from
  `state` and `note`.

Panels, `/apps` and the coordinator's review:

- **Library skills have their own sections.** `Your Skills` (Local) and
  `Built-in Skills` (bundled) come after the catalog's `Skills`, so the skills
  you can add are not buried under the ~50 that ship with Kollab.
- **One "installed" rule** (`service.is_installed`): every `added` row except
  Built-in skills. The panel summary and the web header both use it.
- **Changes reach the running session.** `apply_change` reloads MCP after an
  MCP change and calls `SkillLibrary.refresh()` after a skill add or remove.
- **Long installs.** `RemoteStateService.PANEL_ACTION_TIMEOUTS` gives `apps`
  and `apps-install` actions 630 s (the RPC default is 40 s), so a brew install
  is not cut off while it still runs in the daemon.
- **Terminal list.** `/apps` shows 4 catalog rows per kind plus a count of the
  rest and of the library skills; `/apps search` shows every match.
- **Keys** are set with `/mcp setup`; the reply to a terminal Add says so.
- **Skill archives** are checked only under the skill's own path: a link
  elsewhere in the repo is never extracted, so it does not block the install.
