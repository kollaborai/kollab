# Apps

One place to give your agents more tools: MCP servers, CLI tools and skills.

- **Web UI**: the **Apps** button in the sidebar (above Settings).
- **Terminal**: `/apps`.

## what you can add

| kind | what Add does | where it lands |
|------|---------------|----------------|
| MCP server | saves the server and connects it to the current session | `~/.kollab/mcp/mcp_settings.json` |
| CLI tool | installs it with a package manager you already have (brew, npm, uv or pipx) | your PATH |
| Skill | downloads it from its GitHub repo into the skill library | `~/.kollab/skills/<name>/` |

Everything you add is available to every agent on this computer, and the
session you added it from uses it right away (no restart).

An MCP server that needs a key (a GitHub token, an API key) asks for it before
it is saved. In the terminal, `/apps add` saves such a server turned off and
tells you to set the key with `/mcp setup`, so secrets never go through a
slash command.

A CLI tool that needs a login (`gh auth login`, `vercel login`) tells you the
command to run in your own terminal after it installs.

A skill that drives a CLI tool installs the tool first: Add on the OfficeCLI
Word, Excel or PowerPoint skill installs OfficeCLI, then the skill.

## terminal

```text
/apps                       a few of each kind, and how many more there are
/apps search <words>        every match, by name, description or tag
/apps add <id>              add (e.g. /apps add context7, /apps add cli:gh)
/apps add registry:<name>   show what an MCP Registry server runs, and its code
/apps add registry:<name> <code>   add exactly that
/apps remove <id>           remove an MCP server or a skill added from Apps
```

Ids are `<kind>:<id>` (`mcp:github`, `cli:gh`, `skill:pdf`); the bare id works
when only one kind uses it.

## the MCP Registry

The directory is a curated list. Type two or more letters in the search and
Apps also searches the [official MCP Registry](https://registry.modelcontextprotocol.io)
(about 40,000 servers) and lists its matches under **MCP Registry**, after the
directory's own. Kollab has not reviewed these servers, so:

- **Add** first shows the exact command it will run (**Runs**) and the settings
  it needs. Nothing is saved or started until you press **Add** there. If the
  registry entry changed in the meantime, Add refuses and shows the new command.
- A hosted server runs through `mcp-remote` (`npx -y mcp-remote <url>`), so only
  that bridge runs on your computer. A package runs the publisher's code with
  `npx`, `uvx` or `docker run`.
- Apps won't add a server it can't run safely, and its row says why: a secret on
  a command line, a header or address that would read this computer's
  environment or paths, a setting such as `NODE_OPTIONS` or `npm_config_registry`
  that changes what gets installed, or a docker flag that reaches past the
  container (`-v`, `--privileged`, `--network`).
- An added registry server is listed under **MCP Servers** with a **Registry**
  badge. Remove and Turn On work like any other server.

The registry is sometimes slow: a search takes from under a second to about
30 seconds, and its results are kept for 10 minutes. In the terminal,
`/apps search <words>` lists registry matches after the directory's; it waits up
to 10 seconds, and a slower search keeps going so the same search a moment later
answers at once. `/apps add registry:<name>` shows the command and a code;
nothing is saved until you run it again with the code.

## states

- **Added**: installed (MCP: saved and on; CLI: on your PATH; skill: in the library)
- **Turn On**: an MCP server that is saved but off
- **Add**: available
- **Add (disabled)**: something it needs is missing, e.g. `npx` for a Node server

Skills that ship with Kollab are listed under **Built-in Skills** and are
always there. Skills you wrote yourself (in `~/.kollab/skills` or a project's
`.kollab/skills`) are listed under **Your Skills**. Apps never deletes either;
Remove only deletes a skill folder that Apps downloaded, or an MCP server Apps
added (a server you set up by hand under the same name keeps its config).
**N installed** counts everything added except the built-in skills.

## adding your own entries

Put extra entries in `~/.kollab/apps.json` with the same shape as the bundled
catalog (`bundles/data/apps.json`, schema in `docs/specs/apps-marketplace.md`).
An entry with the same kind and id replaces the bundled one. Check it with
`python scripts/validate_apps.py`.
