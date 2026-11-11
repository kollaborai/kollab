# Engine folder rule

One rule decides which folder an engine request may name (#980). It lives in one helper,
`packages/kollabor-engine/src/kollabor_engine/workspace_folder.py`. Every engine route that takes a folder calls it
first and uses what it returns, never the request's own text.

## The rule

A folder is accepted when, after `~`, relative parts, `..` and symlinks are resolved:

1. it exists and is a directory, and
2. it is inside one of these (the folder itself counts, and so does everything below it):
   - the engine's own folder (its working directory);
   - the user's home folder;
   - a folder an agent on this computer runs in now (hub presence, or a live web session);
   - a folder a web session started in recently (the New Session recent list, `engine-workspaces.json`).

An empty folder means the engine's own. Anything else answers 400 with a sentence the person can act on. The checks
run cheapest first: a folder in home or in the engine's folder never asks which agents are running.

Two details keep the list honest:

- **A remembered folder counts only while its pathname still leads to itself.** The folders agents run in (hub
  presence, live web sessions) and the recent session folders are saved as real paths, with no symlink inside. When
  one is checked later, its real path must still be the path that was saved. If a symlink now stands in its place, or
  in one of its parents, it grants nothing: not the folder, and not the place the link leads. The engine's own
  folder and home are not remembered folders; they are resolved when asked.
- **On a volume that ignores case, a root spelled in other letters is the same root.** macOS and Windows volumes
  usually ignore case, so `/users/me/DEV` is `/Users/me/dev`. A volume that tells them apart (most Linux) keeps them
  apart. The folder is answered spelled the way the volume stores it (`/Users/me/dev/CASEPROJECT` comes back as
  `/Users/me/dev/CaseProject`), because a daemon started there reports that spelling.

## Routes that call it

| Route | Parameter | Notes |
|---|---|---|
| `GET /profiles` | `workspace` | The folder whose default loadout applies (#957). |
| `GET /agents`, `GET /agents/bundles` | `workspace` | The launch pool, and the folder's own `.kollab/agents`. |
| `GET /rosters`, `GET /rosters/{name}/avatars/{file}`, `GET /teams`, `POST /teams/{name}/launch` | `workspace` | Through `rosters.folder_of`. |
| `POST /sessions` | `workspace` | Checked before a persona, an agent or a daemon is looked up; the session starts in the checked path. |
| `GET /workspaces/browse` | `path` | The picker. `parent` is null once going up would leave the rule, so Up stops there. |

## What people see

| They ask for | Result |
|---|---|
| nothing | the engine's own folder |
| `~/dev/app`, `app` (relative), `~/dev/x/../app` | the real path, if it is inside a root |
| `/etc`, `~/../other`, `/` | 400: "Can't use /etc: it is outside your home folder and no kollab agent runs there." |
| a symlink in home that points outside | 400 as above; the message never says where the link points |
| a symlink that points to another folder inside a root | the target's real path |
| a path that is not there | 400: "Folder does not exist: ..." |
| a file | 400: "Not a folder: ..." |
| a NUL byte, or more than 4096 characters | 400: "That is not a folder path." |
| a sibling that only shares a name prefix with a root (`/home/me/proj` and `/home/me/proj2`) | 400: a root matches itself and what is inside it, nothing else |
| a folder outside home and the engine's, with an agent running in it or below it | accepted |
| a folder outside home that nothing runs in | 400. Start kollab in it once (a terminal, or an engine started there); it then opens, and so does everything below it |
| a folder in home typed in other letters (`/USERS/ME/Dev/app` for `/Users/me/dev/app`), on a volume that ignores case | accepted like the right spelling; the answer is spelled the way the volume stores it, the root and what is below it |
| the same on a volume that tells cases apart | 400: it is another name, so it is "outside" or "does not exist" |
| a recent or agent folder outside home that was moved away and replaced by a symlink to another folder | 400 for the old pathname and for the place the link leads; the folder no longer counts |
| a recent or agent folder outside home that was made again, as a real folder, at the same pathname | accepted: the pathname leads to itself again |

## Decisions

- **Gains and losses.** No route can be pointed at `/etc`, another account's files or a system folder through a
  `workspace`. What is lost: the picker can no longer open a folder outside home that kollab has never run in.
- **No new setting.** An outside-home folder gets in the way kollab already knows about folders: an engine or an
  agent started there. Nothing to configure, nothing to approve.
- **A folder that is gone answers 400.** `GET /profiles` used to answer the global default for a missing folder. The
  web UI already keeps the form it has when that fetch for a chosen folder fails.
- **A remembered folder is pinned to the path it was saved as (`_pinned`).** The first version resolved every
  remembered folder again at check time, so a symlink put where an old workspace had been granted access to wherever
  the link led. Now the entry must still be its own real path; a changed one is skipped (fail closed) and the rest of
  the rule runs without it, as does an entry that cannot be a path at all (a NUL byte in a hand-edited list) instead
  of failing the request with a 500. A real folder made again at the same pathname counts again: whoever can do that already
  controls what is at that pathname, and nothing was swapped for another place. The check is a string comparison of
  `realpath` with `normpath`, so it needs no stored inode and survives a restart.
- **Case is asked of the root's volume, never of the request (`_ignores_case`, `_respelled`).** Roots were compared
  as spelled, so an existing folder typed in other letters was refused on macOS, and New Session inherited that. The
  volume answers by whether the root with its case swapped is the same folder (`samefile`); only then is the start of
  the request that names the root rewritten to the root's spelling, and the usual `startswith` test runs on the
  result. A request can only be moved onto a root it already names in other letters, so nothing widens, and a
  volume that tells cases apart is never asked to ignore them. The answer is not cached; it costs two `stat` calls
  for each accepted folder.
- **The answer is spelled the way the volume stores it (`_on_disk`).** A volume that ignores case keeps the spelling
  that was typed in `realpath`, but a daemon started in that folder reports the stored one (its working folder), and
  New Session waits for the agent it launched by comparing the two folder strings
  (`HubBridge.get_agent_by_identity`). A folder typed as `cASEpROJECT` made it wait 45 s and answer 503 while the daemon
  ran, on the version before this rule too (found in the post-merge review of #982). The producer is fixed, not the
  lookup: every part below the matching root is looked up in its folder and replaced by the entry the volume says is
  the same one (`samefile`), so the engine, the recent list and every agent row hold one spelling. A part that is
  already an entry is kept without a lookup, a part with no entry stays as typed, and so does a part on a mount that
  tells cases apart (`samefile` finds no such name) or in a folder that cannot be listed. It runs only where
  `_ignores_case(root)` says the volume ignores case. A remembered folder recorded in the old spelling keeps it until
  it is forgotten.
- **One helper, so one sanitizer.** CodeQL's `py/path-injection` accepts a path that was normalized with
  `os.path.realpath` and then checked with `startswith` against a root. That check lives only in `checked_folder`;
  the existence and directory tests run after it, inside it. The respelling feeds that same check (`spelled`).
- **Not covered.** `GET /network/halt/{name}?folder=` keeps its own absolute-path check: it is the kill switch, and
  no alert reaches it. Folders that agents report about themselves (hub presence) are not requests.

## Adding a route

Take the folder from the request, call `folder_or_400(value)` (or `checked_folder(value)` where there is no HTTP
error to raise), and use only what it returns:

```python
folder = folder_or_400(workspace)   # "" is the engine's own folder
```

`packages/kollabor-engine/tests/test_workspace_folder.py` covers the rule; `test_workspace_folder_routes.py` beside it
lists every route above, so a new one belongs in its `FOLDER_ROUTES`.
