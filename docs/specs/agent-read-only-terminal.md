# Agent read-only terminal tool

Issue: #889
Status: Implemented
Owner: `packages/kollabor-agent`

## Problem
Agent bundles can grant `terminal`, which can execute commands that modify the
workspace, or omit terminal access and lose useful repository inspection. Issue
#889 describes the need for a bundle-level option that permits approved reads
while refusing writes.

## Goal and non-goals
Goal: allow a bundle to explicitly grant a `terminal-read` tool that runs only
commands accepted by Kollab's existing read-only command policy.

Non-goals: changing the behavior of `terminal`; allowing arbitrary shell syntax,
background sessions, commands outside the project, or a second read-only policy.

## What people see
An agent bundle may list `terminal-read` alongside `file-read` and `file-grep`.
For example, `terminal-read(command='git log --oneline -1')` returns command
output when allowed. A refused command returns a reason, such as
`Read-only terminal refused command: command is not classified as read-only`.

## Contract
- Tool name: `terminal-read`; the model-facing tool type is `terminal_read`.
- Required input: non-empty `command` string. Optional input: `cwd`, which must
  resolve inside the project workspace.
- The executor rejects compound shell syntax, unsupported escaping, background
  requests, path-qualified executables, hard-blocked commands, Git operations
  that leave the project, recursive or symlink-following `ls`, and commands the
  existing read-only classifier does not accept.
- Accepted commands are parsed to argv and executed without a shell, with the
  existing terminal timeout, command-size, and output-size limits.
- The tool is explicitly granted through the agent bundle tool list. It does
  not change the permissions of the existing `terminal` tool.
- Owning implementation: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/terminal.py`
  and `packages/kollabor-agent/src/kollabor_agent/tool_executor.py`; policy is
  shared with `packages/kollabor-agent/src/kollabor_agent/voice_tool_policy.py`.

## Scenarios
- If a bundle grants `terminal-read` and the agent requests `git log --oneline -1`
  in the project, then the command runs and its output is returned.
- If the agent requests `touch x.txt`, then the tool refuses the command and no
  file is created.
- If the requested working directory resolves outside the project, then the
  tool refuses the command.
- If a command uses compound shell operators or background execution, then the
  tool refuses it before execution.
- If `ls` requests recursive traversal or symlink dereferencing, then the tool
  refuses it before execution, including when no explicit path is supplied.
- If a command is not recognized by the read-only classifier, then the tool
  fails closed and returns the refusal reason.

## Failure modes
- Empty or malformed command: refuse without executing.
- Command exceeds the read-only policy size limit or is invalid UTF-8: refuse.
- Unsupported shell syntax, escaping, or executable path: refuse.
- Recursive or symlink-following `ls`: refuse.
- Working directory escapes the project or cannot be resolved: refuse.
- Hard-blocked, out-of-project Git, or unclassified command: refuse.
- Allowed command times out or exits unsuccessfully: return failure details and
  stderr/error output.

## Decisions
- Scope decision: issue #889 defines the requested bundle tool and read/refuse
  behavior; its author is not present in the available issue snapshot. The
  implementation reuses the existing read-only classifier so policy has one
  source, while preserving the existing unrestricted `terminal` grant.

## Test plan
- `tests/unit/llm/test_tool_executor.py::TestToolExecutor.test_terminal_read_only_tool_allows_reads_and_refuses_writes`
  verifies allowed argv dispatch, refusal, and no file creation.
- `tests/tmux/specs/agent_terminal_read.json` covers the agent-facing read and
  refusal flow.
- Verify the command executes without a shell, the working directory remains
  project-rooted, and configured timeout/output bounds are passed to execution.
- Sibling surfaces: bundle tool registration and scope filtering; existing
  `terminal` behavior remains independently covered by its executor tests.

## Docs and changelog fragment
- This specification: `docs/specs/agent-read-only-terminal.md`.
- Changelog entry: `changes/889.added.md`.

## Open questions
None.
