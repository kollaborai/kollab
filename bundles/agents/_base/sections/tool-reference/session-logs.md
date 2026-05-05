session logs and conversation history:

all session data lives under a per-project directory:
  ~/.kollab/projects/<encoded-project-path>/

  for example, in ~/dev/kollab:
    ~/.kollab/projects/Users_..._dev_kollab/

directory layout:
  logs/kollab.log                  -- application log (rotated daily)
  conversations/session_*.jsonl      -- conversation history (one per session)
  conversations/memory/              -- persistent memory storage

application log (kollab.log):
  - structured log output from all modules
  - rotated daily (1 backup kept)
  - format: timestamp - LEVEL - message - file:line
  - useful for debugging startup issues, plugin loading, MCP connection errors
  - view with: tail -f ~/.kollab/projects/<encoded-path>/logs/kollab.log

conversation logs (session_*.jsonl):
  - structured JSONL with full message history
  - one file per session, named by timestamp + generated name
  - includes user messages, assistant responses, tool calls, metadata
  - resume past sessions with: /resume

finding the right project directory:
  the project path is encoded by replacing / with _
  e.g. /Users/username/dev/kollab -> Users_username_dev_kollab

  list all projects:  ls ~/.kollab/projects/
  find current:       echo ~/.kollab/projects/$(pwd | tr '/' '_')

quick commands:
  tail -f .../logs/kollab.log          live application log
  ls .../conversations/session_*.jsonl   list conversation files
  /resume                                resume a past conversation
  /save                                  save current conversation

note: the encoded path strips the leading slash, so /Users/foo becomes Users_foo

peer session logs (hub):
  when the hub is active, each agent's presence file includes a session_log
  field pointing to their conversation JSONL file. you can read any peer's
  session log to review their conversation history.

  presence files live at:
    ~/.kollab/hub/presence/<agent_id>.json

  the session_log field contains the full path to the agent's JSONL file:
    "session_log": "~/.kollab/projects/Users_foo_dev_bar/conversations/2604121812-neural-spark.jsonl"

  to find a specific peer's session log:
    1. read their presence file from ~/.kollab/hub/presence/
    2. extract the session_log field
    3. read the JSONL file for full conversation history
