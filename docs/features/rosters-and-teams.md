# Rosters and Teams

Agent names do not have to be gems. A **roster** is a set of personas (a name, a
look, a bundle, a prompt); a **team** puts personas in a reporting chain. Both are JSON.

```bash
kollab --as maya                    # a persona from any roster
kollab --as marketing-crew/maya     # when two rosters share a name
kollab --hub org marketing          # launch a team (also: /hub org marketing "launch the beta")
```

In a chat: `/hub orgs` lists the teams.

## Make your own

Put a file in `~/.kollab/rosters/` (all your folders) or `.kollab/rosters/` (one folder):

```json
{
  "name": "studio",
  "personas": [
    {"name": "ada", "display_name": "Ada", "agent": "coder", "role": "Engineer",
     "avatar": {"kind": "emoji", "value": "A"}, "color": [90, 140, 220],
     "prompt": "You build small, tested changes and say what you verified."},
    {"name": "bo", "display_name": "Bo", "agent": "research", "role": "Reviewer"}
  ]
}
```

and a team in `~/.kollab/teams/` or `.kollab/teams/`:

```json
{"name": "duo", "roster": "studio", "lead": "ada",
 "members": [{"persona": "ada"}, {"persona": "bo", "reports_to": "ada"}]}
```

Make a folder draw its agent names from a roster with `plugins.hub.roster` in
`.kollab/config.json` (or the global config); the default is `gems`.

A file with the same `name` as a bundled one replaces it. A broken file never stops
Kollab: the bad persona or file is skipped and listed under `problems` in
`GET /rosters` and `GET /teams`.

In the web UI, New Session's **Agent** field lists the folder's roster first and every
other roster's personas after it (each with its picture and job title); picking one
fills in its **Type**. The sidebar's **Agents** button shows every roster and team and
launches a team into a folder (**Launch Team**). Rows, the chat header and the settings
call a persona by its display name.

Web/engine: `GET /rosters`, `GET /teams`, `POST /teams/<name>/launch {"workspace": "..."}`.
Contract and every field: [docs/specs/rosters-and-teams.md](../specs/rosters-and-teams.md).
