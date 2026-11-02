# Local and discovery tools

Registry definitions live under `packages/kollabor-agent/src/kollabor_agent/tool_definitions/`. Native names are headings; the XML name is shown where it differs. `git` is documentation-only.

## Permission and reading this catalog

Registry risk and confirmation flags below are metadata, not a promise that a
prompt will or will not appear. The risk assessor reads those fields, while the
permission manager chooses whether to prompt from risk, tool type, mode and
prior approvals; the flag alone does not settle that decision. Trusted/blocked
rules, terminal command patterns, hooks, voice guards and service checks also
matter. Follow the actual decision and the user's authorization. Source:
`packages/kollabor-agent/src/kollabor_agent/permissions/risk_assessor.py`,
`packages/kollabor-agent/src/kollabor_agent/permissions/manager.py`.

### `file_mkdir`

Purpose: Create a directory (including parent directories). XML tag: `mkdir`.
Inputs: Required: `path`. Optional: none.
Result: Confirmation that directory was created.
Effects: Creates the directory and any required parents.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `file_rmdir`

Purpose: Remove an empty directory. XML tag: `rmdir`.
Inputs: Required: `path`. Optional: none.
Result: Confirmation that directory was removed. Fails if not empty.
Effects: Removes an empty directory.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `file_append`

Purpose: Append content to the end of a file. XML tag: `append`.
Inputs: Required: `file`, `content`. Optional: none.
Result: Confirmation that content was appended.
Effects: Writes content at the end of the file.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `file_copy`

Purpose: Copy a file. Fails if destination exists. XML tag: `copy`.
Inputs: Required: `from`, `to`. Optional: none.
Result: Confirmation that file was copied. Fails if destination exists.
Effects: Copies to a new destination; it fails if the destination exists.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `file_copy_overwrite`

Purpose: Copy a file, overwriting destination if it exists. XML tag: `copy_overwrite`.
Inputs: Required: `from`, `to`. Optional: none.
Result: Confirmation that file was copied, overwriting destination.
Effects: Copies to and may overwrite the destination.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `file_create`

Purpose: Create a new file with content. Fails if file already exists. XML tag: `create`.
Inputs: Required: `file`, `content`. Optional: none.
Result: Confirms file created with path.
Effects: Creates a file; fails if it already exists.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `file_create_overwrite`

Purpose: Create or overwrite a file with content. XML tag: `create_overwrite`.
Inputs: Required: `file`, `content`. Optional: none.
Result: Confirms file created or overwritten with path.
Effects: Creates or overwrites the file. If automatic backup is enabled and succeeds, the temporary pre-write backup is discarded after a successful write; do not assume a retained recovery copy.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `file_delete`

Purpose: Delete a file. XML tag: `delete`.
Inputs: Required: `file`. Optional: none.
Result: Confirmation that file was deleted.
Effects: Deletes the named file. When automatic backup succeeds, the result reports a retained recovery copy; backup can be disabled or fail.
Permission: registry risk `high`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `file_edit`

Purpose: Find and replace text in a file. Replaces ALL occurrences of the pattern. XML tag: `edit`.
Inputs: Required: `file`, `find`, `replace`. Optional: none.
Result: Reports number of replacements made.
Effects: Writes the file and replaces every exact match, not just the first.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `file_grep`

Purpose: Search for a pattern in a file and return matching lines. XML tag: `grep`.
Inputs: Required: `file`, `pattern`. Optional: `case_insensitive`.
Result: Matching lines from the file with line numbers.
Effects: Reads a file to find matching lines.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `file_insert_after`

Purpose: Insert content after a pattern in a file. Pattern must match exactly. XML tag: `insert_after`.
Inputs: Required: `file`, `pattern`, `content`. Optional: none.
Result: Confirmation that content was inserted after the pattern.
Effects: Writes content after an exact match.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `file_insert_before`

Purpose: Insert content before a pattern in a file. Pattern must match exactly. XML tag: `insert_before`.
Inputs: Required: `file`, `pattern`, `content`. Optional: none.
Result: Confirmation that content was inserted before the pattern.
Effects: Writes content before an exact match.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `file_move`

Purpose: Move or rename a file. XML tag: `move`.
Inputs: Required: `from`, `to`. Optional: none.
Result: Confirmation that file was moved/renamed.
Effects: Moves or renames the source path.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `file_read`

Purpose: Read content from a file. Use this to examine existing files before editing. XML tag: `read`.
Inputs: Required: `file`. Optional: `offset`, `limit`.
Result: On success: file content with a success header showing path and line count. On error: 'error: <reason>'
Effects: Reads the named file; binary files are rejected.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`, `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`.

### `git`

Purpose: Documentation-only Git entry, mapped to the terminal tool rather than exposed as a separate native tool. XML tag: `terminal`.
Inputs: Required: `command`. Optional: none.
Result: Standard git command output via terminal.
Effects: Runs the requested Git command through terminal, with whatever effects that command has.
Permission: registry risk `medium`, confirmation flag `false`; the actual terminal command is assessed under terminal rules.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/git.py`, `packages/kollabor-agent/src/kollabor_agent/permissions/risk_assessor.py`.

### `mcp_reload`

Purpose: Reload MCP server connections and rediscover tools. Use this when MCP configuration has changed or new servers have been added. Closes all active connections, reloads config files, and reconnects enabled servers. XML tag: `mcp-reload`.
Inputs: Required: none. Optional: none.
Result: On success: summary with configured, discovered, and reconnected server counts. On failure: error message.
Effects: Closes existing MCP connections, reloads configuration, and reconnects enabled servers.
Permission: registry risk `medium`, confirmation flag `true`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/mcp.py`.

### `terminal`

Purpose: Execute a terminal/shell command and return the output.
Inputs: Required: `command`. Optional: `background`, `name`, `timeout`, `cwd`.
Result: On success: command stdout (raw). On non-zero exit: 'Exit code N: <stderr>'.
Effects: Executes the command with its real filesystem, process, and network effects; a background call starts a persistent subprocess.
Permission: registry risk `high`, confirmation flag `true`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/terminal.py`, `packages/kollabor-agent/src/kollabor_agent/tool_executor.py`.

### `terminal_kill`

Purpose: Kill a background terminal session or all sessions. XML tag: `terminal-kill`.
Inputs: Required: `name`. Optional: none.
Result: Confirmation that session(s) were killed.
Effects: Terminates the named background session; `*` targets all sessions.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/terminal.py`, `packages/kollabor-agent/src/kollabor_agent/tool_executor.py`.

### `terminal_output`

Purpose: Capture recent output from a background terminal session. XML tag: `terminal-output`.
Inputs: Required: `name`. Optional: `lines`.
Result: Recent output from the session.
Effects: Reads retained output of a subprocess session.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/terminal.py`, `packages/kollabor-agent/src/kollabor_agent/tool_executor.py`.

### `terminal_status`

Purpose: List or check status of background terminal sessions. XML tag: `terminal-status`.
Inputs: Required: `name`. Optional: none.
Result: Status of matching session(s).
Effects: Reads subprocess session state.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/terminal.py`, `packages/kollabor-agent/src/kollabor_agent/tool_executor.py`.

### `tool_load`

Purpose: Load a built-in tool definition or enable a connected MCP tool for this session. Other scope and permission checks still apply. XML tag: `tool-load`.
Inputs: Required: `name`. Optional: none.
Result: Built-ins return registry documentation; MCP entries return the server name, description, and the available `parameters` schema, which can be empty. Errors explain missing tools or disconnected servers.
Effects: Expands the active built-in scope or enables a connected MCP entry and refreshes native schemas; it does not grant user authorization.
Permission: registry risk `medium`, confirmation flag `true`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/on_demand.py`, `packages/kollabor-agent/src/kollabor_agent/tool_executor.py`.

### `tool_search`

Purpose: Search for available tools by keyword. Returns a compact list of matching tools (name, category, and one-line description) without loading their full schemas. Use this to discover tools you need, then call tool-load to activate them. XML tag: `tool-search`.
Inputs: Required: none. Optional: `query`.
Result: List of matching tools, each with: name, category, source (built-in or MCP server name), and a one-line description. Results are ranked by relevance. Empty list if no matches.
Effects: Reads the built-in registry and enabled discovered MCP registry; a match does not establish authorization.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/on_demand.py`.

### `voice_out`

Purpose: Speak an intentional update during an active voice turn through the shared device queue.
Inputs: Required: `text`. Optional: none.
Result: JSON playback status, or a voice-turn/format error.
Effects: Queues spoken audio on the active voice output device; it does not activate a microphone.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/voice.py`, `plugins/voice_plugin.py`.

### `web_fetch`

Purpose: Fetch a URL and return the content as clean text. Extracts main content using readability heuristics — returns clean text without navigation, ads, or page chrome. XML tag: `web-fetch`.
Inputs: Required: `url`. Optional: `max_chars`, `extract_main`.
Result: On success: clean text content from the page, truncated to max_chars. On failure: error message describing what went wrong.
Effects: Makes a network request to the supplied URL.
Permission: registry risk `medium`, confirmation flag `true`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/web.py`.

### `web_search`

Purpose: Search the web and return results. Returns titles, URLs, and snippets for the top matches. XML tag: `web-search`.
Inputs: Required: `query`. Optional: `max_results`.
Result: Formatted list of search results, each with title, URL, and a short snippet. Typically ~200 chars per result.
Effects: Sends the query to DuckDuckGo's HTML endpoint.
Permission: registry risk `medium`, confirmation flag `true`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/web.py`, `packages/kollabor-agent/src/kollabor_agent/tool_executor.py`.

### `workspace_set`

Purpose: Switch the agent's working directory. Updates the workspace for all subsequent terminal commands and file operations. The status bar cwd widget updates immediately. XML tag: `workspace-set`.
Inputs: Required: `path`. Optional: none.
Result: On success: confirmation message with the resolved absolute path. On failure: error message explaining why the switch failed.
Effects: Changes the working directory used by subsequent terminal commands and file operations.
Permission: registry risk `medium`, confirmation flag `true`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/workspace.py`.
