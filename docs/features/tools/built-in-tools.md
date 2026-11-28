---
title: "Built-in Tools"
created: 2026-02-24
modified: 2026-11-27
status: active
---
## Built-in Tools

note: these are XML tags parsed by kollabor_ai.response_parser, not tools/
  directory entries. execution happens in packages/kollabor-agent/.

### Web Fetch

`web-fetch` compares supported JSON-LD article bodies with semantic HTML and
readable page text before selecting the fullest candidate. It uses a JSON-LD
description only when that description is longer than the extracted page text,
so metadata does not mask fuller article content. Fallback text extraction
removes script, style, `pre`, and `code` bodies. The agent package installs
Beautiful Soup and uses Python's built-in HTML parser; lxml is not required. If
Beautiful Soup is unavailable, the regex fallback strips script, style, `pre`,
and `code` contents but does not extract JSON-LD.

### Terminal Commands

The LLM can execute shell commands through the `terminal` XML tag.

XML tag syntax:
```
<terminal>ls -la</terminal>
```

Background execution:
```
<terminal background="true" name="dev-server">npm run dev</terminal>
```

On POSIX, background terminal commands run through the configured user shell,
so assignments, command substitutions, pipes, and redirections work as they do
for foreground terminal commands.

Use `file-find` for filename discovery in the active workspace:

```xml
<file-find><pattern>*.py</pattern></file-find>
```

The native tool is `file_find(pattern="*.py")`. It accepts a basename glob,
with no root path, working-directory override, command or session input.
It lists matching files and directories as workspace-relative names without
running a shell or following symlinks. A symlink's own name may match.
The JSON result contains `matches` and `truncated`; when `truncated` is true,
the search reached its traversal or output limit and the list is incomplete.
Errors, including permission failures, are returned explicitly. Platforms
without safe directory descriptor traversal refuse the search.

Ask the user before searching another folder and switch workspaces only after
they grant that folder. Do not start exploratory terminal searches at home,
`/Users`, `/`, or a parent directory. Direct `find` commands, including those
in shell command sequences, are restricted to the active workspace; searches
rooted outside it, symlink-following searches, and roots loaded from a file are
refused. Other `terminal` commands and
`terminal-send` input run with the user's filesystem access and do not enforce
a workspace sandbox. Commands wrapped in interpreters or scripts are not
restricted by the `find` check.

With working directory:
```
<terminal cwd="/tmp">pwd</terminal>
```

#### Kollab commands from agent terminals

Agents may run Kollab commands from terminals by default, including background
and informational ones such as `kollab --hub status`. An owner who wants agent
terminals kept off the Kollab CLI can opt in:

```json
{"tools": {"block_kollab_terminal_spawn": true}}
```

The setting defaults to `false`. When it is `true`, agent terminals refuse:

- agent launches (`--agent`, `--detached`), including through wrappers such as
  `bash -c`, `env`, `nohup`, or `python -c`;
- the stop-all command `kollab --hub stop all`;
- any Kollab command started in the background, including informational ones.

The refusal explains the alternative: run informational commands in the
foreground, use `<hub_spawn>` to start peers, and use `/hub stop all` to stop
agents owned by the current caller. The `kollab` CLI applies the same setting
to agent launches started from a tool shell.

When this setting is enabled, the terminal tool's shell-text guard scans
commands before quote context is lost. Quoted command and process-substitution
examples are treated as literal text; shell-executed substitutions remain
checked for blocked launches. This guard is a shell-text heuristic, not an OS
sandbox.

Execution methods:
- tmux sessions (preferred, supports background jobs)
- direct subprocess (fallback)

The `timeout` attribute sets a foreground command's maximum runtime. It can
shorten but cannot exceed the configured terminal timeout; excessive and invalid
values are rejected before execution. Voice-origin commands may have a stricter
policy limit. Durations use seconds, minutes, or hours (`30s`, `5m`, `1h`). For a
background session, `timeout` schedules an auto-kill. Without it, the session has
no automatic timeout.

Agent bundles that need terminal inspection without shell writes can grant the
`terminal-read` tool alongside `file-read` and `file-grep`:

```json
{"tools": ["file-read", "file-grep", "terminal-read"]}
```

Call it with a read-only command such as `git log --oneline -1`. It parses the
command into argv and runs it without a shell. Writes, unsupported shell syntax,
untrusted executables, and working directories outside the project are refused
with a reason. The existing `terminal` tool keeps its normal behavior. See the
[read-only terminal specification](../../specs/agent-read-only-terminal.md) for
the full policy.

A bundle that needs Git inspection without general terminal access can grant
`git` instead:

```json
{"tools": ["file-read", "file-grep", "git"]}
```

This permits one read-only Git command at a time, such as `git status --short`,
including through XML `<terminal>` requests. The command uses the same
parsed-argv policy; aliases, shell syntax, pager or external-helper options,
credential commands, Git writes (including commits that could run repository
hooks), remote transports, and other terminal commands are refused. Grant
`terminal` separately when the bundle needs shell commands or Git writes.

### Workspace Switching

`workspace-set` changes the agent's session workspace at runtime:

```xml
<workspace-set><path>../sibling-project</path></workspace-set>
```

The path must exist and be a directory. Relative paths resolve from the current
workspace; after a successful switch, file operations and terminal commands
without an explicit `cwd` use the new directory. The change is session-scoped
and requires the normal medium-risk tool approval.

### File Operations

The LLM can read, create, and modify files. All file operations go through
the FileOperationsExecutor with safety features:
- Automatic backups before destructive changes
- Protected path checking
- Binary file detection
- Size limits

Text operations (`file_read`, `file_grep`, `file_edit`, `file_append`,
`file_insert_after`, and `file_insert_before`) require a regular file. They
report directories and other non-regular files as tool errors before checking
size or probing content. Filesystem errors retain their reason; only decoding
failures are classified as binary. Symlinks follow the normal path-access rules.
`file_grep` searches a single file and does not recurse into directories.

XML tag syntax:

Read file:
```
<read>
<path>src/main.py</path>
</read>
```

Edit file (replaces ALL occurrences):
```
<edit>
<path>config.json</path>
<old_str>"timeout": 30</old_str>
<new_str>"timeout": 60</new_str>
</edit>
```

Create new file:
```
<create>
<path>new_file.py</path>
<content>
print("hello world")
</content>
</create>
```

Create or overwrite:
```
<create_overwrite>
<path>output.txt</path>
<content>content</content>
</create_overwrite>
```

Delete file:
```
<delete>
<path>old_file.txt</path>
</delete>
```

Move/rename:
```
<move>
<path>old_name.py</path>
<destination>new_name.py</destination>
</move>
```

Copy:
```
<copy>
<path>source.py</path>
<destination>backup.py</destination>
</copy>
```

Copy with overwrite:
```
<copy_overwrite>
<path>source.py</path>
<destination>backup.py</destination>
</copy_overwrite>
```

Append:
```
<append>
<path>log.txt</append>
<content>New log entry</content>
</append>
```

Insert after pattern:
```
<insert_after>
<path>config.py</path>
<pattern>import os</pattern>
<content>import sys</content>
</insert_after>
```

Insert before pattern:
```
<insert_before>
<path>config.py</path>
<pattern>if __name__</pattern>
<content># Main entry point</content>
</insert_before>
```

Create directory:
```
<mkdir>
<path>new_directory</path>
</mkdir>
```

Remove directory:
```
<rmdir>
<path>empty_directory</path>
</rmdir>
```

Search file:
```
<grep>
<path>main.py</path>
<pattern>TODO</pattern>
</grep>
```

### MCP Tools

MCP (Model Context Protocol) servers provide external tools. See [mcp.md](../mcp.md)
for full details.

Common MCP tools:
- `filesystem::read_file` - Read file contents
- `filesystem::write_file` - Write files
- `filesystem::create_directory` - Create directories
- `git::git_status` - Git status
- `git::git_log` - Git log
- `git::git_commit` - Commit changes
- `github::create_issue` - Create GitHub issues
- `github::create_pull_request` - Create PRs
- `brave_search::brave_web_search` - Web search
- `sqlite::query_sqlite_db` - Database queries
- `puppeteer::*` - Browser automation

