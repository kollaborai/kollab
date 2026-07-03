session logs and self-diagnosis

all session data lives under a per-project directory:
  ~/.kollab/projects/<encoded-project-path>/

  for example, in ~/dev/kollab:
    ~/.kollab/projects/Users_malmazan_dev_kollab/

  the project path is encoded by replacing / with _
  e.g. /Users/username/dev/kollab -> Users_username_dev_kollab

  find YOUR project dir right now:
    echo ~/.kollab/projects/$(pwd | tr '/' '_')

directory layout:
  logs/kollab.log                  -- application log (rotated daily)
  conversations/session_*.jsonl    -- conversation history (one per session)
  conversations/memory/            -- persistent memory storage

finding YOUR session log:
  the current session's JSONL file is the most recently modified one:
    ls -t ~/.kollab/projects/$(pwd | tr '/' '_')/conversations/session_*.jsonl | head -1

  you can read your own conversation history to see what happened:
    <read><file>~/.kollab/projects/<encoded>/conversations/<newest>.jsonl</file></read>

  or tail the last few entries:
    <terminal>tail -5 ~/.kollab/projects/$(pwd | tr '/' '_')/conversations/session_*.jsonl | python -m json.tool</terminal>

application log (kollab.log):
  - structured log output from all modules
  - rotated daily (1 backup kept)
  - format: timestamp - LEVEL - message - file:line
  - this is the FIRST place to look when something goes wrong

  live watch:
    <terminal>tail -f ~/.kollab/projects/$(pwd | tr '/' '_')/logs/kollab.log</terminal>

  last 50 lines:
    <terminal>tail -50 ~/.kollab/projects/$(pwd | tr '/' '_')/logs/kollab.log</terminal>

  search for errors:
    <terminal>rg "ERROR|WARN|FAILED|Traceback" ~/.kollab/projects/$(pwd | tr '/' '_')/logs/kollab.log</terminal>

self-diagnosis when something feels wrong:

  "did my last tool call fail silently?"
    check the log for FAILED tool executions:
      rg "FAILED" .../logs/kollab.log | tail -10

  "am i stuck in a loop?"
    check for repeated patterns:
      rg "Turn not completed" .../logs/kollab.log | tail -20

  "why did i stop mid-task?"
    the log shows the sequence of tool calls and turn completions.
    a gap in timestamps means the model was waiting for an API response.
    a "FAILED" entry means a tool call errored.
    "Turn completed" means the model stopped calling tools and yielded.

  "what's my current context/token usage?"
    check the HUD or ask the user — the harness tracks this.

quick reference:
  P=$(echo ~/.kollab/projects/$(pwd | tr '/' '_'))
  tail -f $P/logs/kollab.log              live log
  tail -50 $P/logs/kollab.log             last 50 lines
  ls -t $P/conversations/session_*.jsonl  find current session
  rg "ERROR|FAILED" $P/logs/kollab.log    find errors

peer session logs (hub):
  when the hub is active, each agent's presence file includes a session_log
  field pointing to their conversation JSONL file.

  presence files:
    ~/.kollab/hub/presence/<agent_id>.json

  the session_log field contains the full path:
    "session_log": "~/.kollab/projects/.../conversations/2604121812-neural-spark.jsonl"

  to find a peer's session log:
    1. read their presence file from ~/.kollab/hub/presence/
    2. extract the session_log field
    3. read the JSONL file for their conversation history
