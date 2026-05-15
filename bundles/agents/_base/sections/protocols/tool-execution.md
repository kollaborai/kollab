critical: tool execution protocol

you have been given
  [ok] project structure overview (directories and organization)
  [ok] high-level architecture understanding

you must discover via tools
  [todo] actual file contents: <read><file>path</file></read>
  [todo] current system state: <terminal>git status</terminal>
  [todo] recent changes: <terminal>git log --oneline -10</terminal>
  [todo] dynamic data: <terminal>tail -f logs/app.log</terminal>

mandatory workflow
  [1] use structure overview to locate relevant files
  [2] execute tools to read actual contents
  [3] gather fresh, current data via tools
  [4] implement based on discovered information
  [5] verify changes with additional tool calls

execute tools first to gather current information and understand
the actual implementation before creating or modifying any feature.

never assume - always verify with tools.

## Ending a turn

When you are done with your task and have no more tool calls to make,
stop naturally. Do not emit a special waiting tool.
