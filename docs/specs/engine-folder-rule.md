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

## Decisions

- **Gains and losses.** No route can be pointed at `/etc`, another account's files or a system folder through a
  `workspace`. What is lost: the picker can no longer open a folder outside home that kollab has never run in.
- **No new setting.** An outside-home folder gets in the way kollab already knows about folders: an engine or an
  agent started there. Nothing to configure, nothing to approve.
- **A folder that is gone answers 400.** `GET /profiles` used to answer the global default for a missing folder. The
  web UI already keeps the form it has when that fetch for a chosen folder fails.
- **One helper, so one sanitizer.** CodeQL's `py/path-injection` accepts a path that was normalized with
  `os.path.realpath` and then checked with `startswith` against a root. That check lives only in `checked_folder`;
  the existence and directory tests run after it, inside it.
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
