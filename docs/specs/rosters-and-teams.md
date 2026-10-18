# Rosters and Teams

Status: implemented: backend, CLI and web UI.

An agent's name is not tied to gems. A **persona** is one launchable identity; a
**roster** is a themed set of personas (the 24 gems, a marketing crew, your own
cast); a **team** wires personas into a reporting chain. All three are plain JSON.

Code: `plugins/hub/rosters.py` (loader and lookup), `plugins/hub/org_launcher.py`
(CLI/TUI team launch), `packages/kollabor-engine/src/kollabor_engine/routes/rosters.py`
and `routes/teams.py` (API). Bundled data: `bundles/rosters/`, `bundles/teams/`.

## Persona

| field | type | meaning |
|---|---|---|
| `name` | slug `[a-z][a-z0-9-]*` | required. The hub designation: what `--as <name>` and `hub_msg to=` use |
| `display_name` | string | what a UI shows; default: `name` in Title Case |
| `avatar` | object | see Avatars; default: the roster's, else `{"kind": "initials"}` |
| `color` | `[r, g, b]` 0-255 | message tag and avatar tint; default: a palette color picked by the name (stable) |
| `agent` | string | agent bundle it runs (`coder`, `research`, ...); empty = the default bundle |
| `role` | string | job title; shown, and told to the agent |
| `personality` | string | one-line description (shown in lists; not added to the prompt) |
| `prompt` | string | added to the agent's hub context when it runs under this name |
| `skills` | list of strings | skills attached on top of the bundle when the agent starts |
| `profile` | string | LLM profile it prefers; used when the launch request names none |
| `caste`, `role_aliases` | string, list | optional; the gems use them |

An unknown field is ignored. A persona with an invalid field is skipped and
reported in `problems`; the rest of its roster loads.

## Roster file

```json
{
  "name": "marketing-crew",
  "display_name": "Marketing Crew",
  "description": "A small marketing team",
  "avatar": {"kind": "initials"},
  "personas": [
    {"name": "maya", "display_name": "Maya Lindqvist", "avatar": {"kind": "emoji", "value": "..."},
     "color": [92, 138, 214], "agent": "research", "role": "Brand Strategist", "prompt": "..."}
  ]
}
```

`name` defaults to the file name. `avatar` is the default for personas that set none.
A roster with no valid persona is dropped (reported). Bundled: `gems` (avatar kind
`gem`, all `agent: coder`) and `marketing-crew` (an example with initials and emoji).

## Team file

```json
{
  "name": "marketing",
  "description": "...",
  "roster": "marketing-crew",
  "lead": "omar",
  "members": [
    {"persona": "omar", "reports_to": null},
    {"persona": "maya", "reports_to": "omar"},
    {"persona": "jonas", "reports_to": "theo", "prompt": "Review Theo's drafts first."},
    {"persona": {"name": "bot-1", "display_name": "Bot One"}, "role": "Helper", "agent": "coder", "reports_to": "omar"}
  ]
}
```

- `roster`: where bare `persona` names are looked up first (then every roster).
  A member may also write `"roster/name"`.
- `persona` is a name, or an **inline persona** (an object with at least `name`) for a
  seat that is in no roster. Inline personas are found by name afterwards too.
- `role`, `agent`, `profile` default to the persona's; `prompt` is the team's
  instructions for that seat.
- `lead` must be a member. `reports_to` names another member (or null). A member twice,
  a missing reference or a reporting loop drops the whole team (reported).
- Launch order: the lead, then each member after the one they report to; listing
  order within a level.

`engineering` and `startup` (the former org charts) and `marketing` ship bundled.

## Locations and resolution

Like agents and skills; a later file replaces an earlier one **of the same `name`**
(a whole roster or team, no merging):

1. bundled `bundles/rosters/*.json`, `bundles/teams/*.json`
2. user `~/.kollab/rosters/`, `~/.kollab/teams/`
3. project `.kollab/rosters/`, `.kollab/teams/` (of the folder the agent runs in)

Files are re-read when they change; a running agent needs no restart. The old
`~/.kollab/hub/pool.json` and `~/.kollab/hub/organizations/` lookups are gone (none
existed on the maintainer's machines).

## A folder's roster and names

`plugins.hub.roster` (config, project `.kollab/config.json` over global; default
`gems`) names the roster a folder draws from. The hub hands out the next free name
from it in file order; once all are live it numbers them (`omar-2`).
Pick it in `/config` (Hub, Roster; Ctrl+S then L keeps it in the folder's `.kollab/config.json`).

`kollab --as <name>` takes a persona from **any** roster. A bare name prefers the
folder's roster, then the others in load order; `--as roster/name` picks one. The
hub designation is the part after the slash. A numbered name (`lapis-2`) wears its
base persona. Every folder has its own hub, so a name only has to be free in its folder.

## What a persona does when it runs

- `agent` is the bundle (`desired_bundle_for_identity`): a plain `kollab --as maya`
  becomes `research`; an explicit `--agent` wins. The web `POST /sessions {"identity"}`
  fills `agent` and `profile` from the persona when the request leaves them out; a
  request that names no profile anywhere starts on the active profile.
- `role`, `display_name` and `prompt` are added to the hub context (the first system
  message), next to "you are <name> on the kollabor hub". A persona with neither role
  nor prompt (the gems) adds nothing.
- `skills` attach once the bundle is settled, whichever way the agent was launched:
  `--as`, `POST /sessions`, a team launch and `hub_spawn` all end in a `kollab --as`
  process, and the hub applies them there (`attach_persona_skills`). A skill the bundle
  declares activates as is; any other comes from the skill library (bundled, `~/.kollab/skills`,
  `.kollab/skills`), as the `/skills` picker does. One that is in neither is named in a
  warning line in the agent's window and the rest still attach. A skill already loaded by
  `--skill` is left alone, and an ACP harness agent gets none.

**Launch brief.** A launcher can give an agent extra instructions in the
`KOLLAB_LAUNCH_BRIEF` environment variable (a team seat's instructions and its
reporting chain); the hub adds the text to the same context. `POST /sessions`
takes it as `launch_brief`. (`--system-prompt` cannot carry this: an active agent
bundle's own prompt always wins over it.)

## Avatars

`{"kind": "gem"}` (the 3D gem), `{"kind": "initials"}`, `{"kind": "emoji", "value": "..."}`,
`{"kind": "image", "src": "<path inside the roster file's folder, or https URL>"}`.
A path may not be absolute or contain `..` (the file is rejected at load).

## Engine API

All take an optional `?workspace=<abs folder>` (project rosters/teams and the folder's
roster setting apply; default: the engine's folder; 400 if it is not a folder).

`GET /rosters`

```json
{
  "workspace": "/home/user/work", "default": "gems",
  "rosters": [{
    "name": "marketing-crew", "display_name": "Marketing Crew", "description": "...",
    "avatar": {"kind": "initials"}, "source": "bundled", "path": "/.../marketing-crew.json",
    "personas": [{
      "name": "maya", "roster": "marketing-crew", "display_name": "Maya Lindqvist",
      "avatar": {"kind": "emoji", "value": "..."}, "color": [92, 138, 214],
      "agent": "research", "role": "Brand Strategist", "personality": "...",
      "skills": [], "profile": "", "prompt": "...", "caste": "", "role_aliases": []
    }]
  }],
  "problems": [{"file": "/.../broken.json", "message": "..."}]
}
```

An image avatar is described as `{"kind": "image", "url": "/rosters/<roster>/avatars/<src>"}`
(or the https URL). `GET /rosters/{name}/avatars/{file}` serves it from the roster
file's folder: only png, jpg, gif, webp and svg; anything outside that folder or of
another type is a 404; the response carries a sandboxing Content-Security-Policy.
Browsers cannot attach the bearer token to an `<img>`, so a client fetches it with the
token and uses a blob URL.

`GET /teams`: `{"teams": [{"name", "description", "roster", "lead", "source", "path",
"members": [{"persona": {...as above...}, "role", "agent", "profile", "prompt",
"reports_to"}]}], "problems": [...]}`.

`POST /teams/{name}/launch` with `{"workspace": "/abs/folder", "profile": "optional"}`
starts each member that is not already live **in that folder**, lead first, each through
`POST /sessions` (same code and registry, so they appear in the sidebar). The agent is
the member's, the profile is the request's, else the member's, else none: `POST /sessions`
then picks the active profile, the one a New Session chat starts on.

```json
{"team": "marketing", "workspace": "/abs/folder",
 "started": ["omar", "maya"], "skipped": [{"persona": "theo", "reason": "live"}],
 "failed": [{"persona": "priya", "error": "Profile 'x' not found"}],
 "sessions": [{"session_id": "...", "identity": "marketing-crew/omar", "...": "..."}]}
```

A second call starts nothing new. A failing member is reported and the others still
start. 404 for an unknown team.

`GET /agents` rows (the launch list) gain `roster`, `display_name`, `avatar` and `agent`
(`agent_type` stays); with `?workspace=` the list is that folder's roster.

`GET /sessions` `offline[]` rows (agents that ran in the engine's folder and are not
running) carry `display_name`, `avatar`, `color` and `caste` of the persona their name
resolves to in any roster (a bare name prefers the folder's roster), so a persona
launched from another roster's folder keeps its look when it stops. A name that is no
persona (the koordinator) carries none of them; the client falls back to its pool entry.

## CLI and TUI

`kollab --as roster/name`; `/hub orgs` lists teams; `/hub org <team> [mission]` and
`kollab --hub org <team>` / `--org <team>` launch one (detached processes, each with
its brief). Help text says "team".

A launch starts the members one at a time, lead first, and waits for each to be online
(its presence is live in the folder's hub) before starting the next, so the lead holds
the coordinator seat on a slow start too. A member already online in that folder is left
alone and named (`already online: ...`); when the lead is one of them the mission is not
sent (`kollab --hub msg <lead> <mission>` does that). The launch stops at the first member
that does not come online within 30 s and names it; the same command launches the rest.
`/hub org` and `--org` run it off the event loop, and `/hub org` lists each member under
who they report to. `kollab --hub org` exits 1 when a member fails.

## Web UI

Code: `packages/kollabor-webui/frontend/src/components/agents/` (`Avatar`, `AgentPicker`,
`AgentLibrary`, `launch-choices.ts`, `team-model.ts`), `hooks/use-rosters.ts`.

- **Names.** A persona is called by its `display_name` in rows (live, offline, remote),
  the chat header, Session Controls, Who Is Online and the Session settings tab, and
  falls back to its identity in Title Case (`agentName` in `utils/session-display.ts`).
  Gem Studio dresses gems only. A persona with no gem look has nothing to dress: its
  Properties (row menu, double-click on its picture, or the greeting's picture) show its
  picture and name with the chat's own Hub switch and session facts, and no Eyes, Hat or
  Color (`isGemAvatar` in `avatar-model.ts` tells the two apart).
- **Avatar.** One component draws every agent picture: kind `gem` (or none) is the 3D
  gem; `image`, `emoji` and `initials` are a flat tile on the persona's color. Image
  files are fetched with the bearer token into blob URLs; a file that fails keeps the
  initials.
- **New Session.** *Agent* is one select grouped by roster: the chosen Workspace's roster
  first (from `GET /agents?workspace=`, koordinator first), then each other roster's
  personas from `GET /rosters`, which start as `roster/name`. Each row shows picture,
  name and job title. "In Use" is per folder and by bare name: a name running in the
  Workspace is disabled in every group, one running only in another folder is free.
  Picking a persona preselects its *Type* (the agent bundle; `agent` in the request,
  which the user can override). With no answer from the folder nobody is offered.
- **Agents view.** Rosters tab (personas with Name, Type, Model Profile, Skills,
  Personality, Prompt), Teams tab (outline by `reports_to`, lead marked, Launch Team
  into the chosen folder: Started, Already Running, Failed), and the `problems` grouped
  by file. The folder picker applies project rosters and teams.

## Not built yet

- A `GET /rosters` entry for personas that exist only inside a team.
