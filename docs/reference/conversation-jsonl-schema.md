
Conversation JSONL Schema Reference
====================================

Every kollabor session writes a structured JSONL (JSON Lines) log file
capturing the full conversation: user messages, LLM responses, system
events, tool results, and session metadata.

File Location
-------------

Base path:
  ~/.kollab/projects/{encoded-project-path}/conversations/

The project path is encoded by replacing "/" with "_" and stripping the
leading slash. Example:
  /Users/example/dev/kollab -> Users_example_dev_kollab

File naming:
  {YYMMDDHHMM}-{name}.jsonl       (current format)
  session_{YYYYMMDD_HHMMSS}.jsonl  (legacy format)

Each file is one session. One JSON object per line. Lines are appended
in chronological order.

Source Code
-----------

The authoritative source for all write operations:
  packages/kollabor-ai/src/kollabor_ai/conversation_logger.py

Shared parser (reading):
  packages/kollabor-ai/src/kollabor_ai/session_parser.py


Entry Types
===========

Five entry types exist, distinguished by the "type" field:

  [1] conversation_metadata   session root, written first
  [2] user                    user input messages
  [3] assistant               LLM responses
  [4] system                  hook output, tool results, hub events
  [5] conversation_end        session termination summary


Common Fields (All Entries)
===========================

Every JSONL entry contains these fields:

  Field         Type        Description
  -----------   ----------  ------------------------------------------
  uuid          string      Unique entry identifier (UUID v4)
  timestamp     string      ISO 8601 with "Z" suffix (UTC)
  parentUuid    string|null UUID of parent entry for threading
  isSidechain   boolean     Always false in current implementation
  userType      string      Always "external"
  cwd           string      Working directory at time of entry
  sessionId     string      Session identifier (matches filename stem)
  version       string      Application version (e.g. "0.5.7")
  gitBranch     string      Active git branch (or "unknown")


Entry Type: conversation_metadata
==================================

Written as the second line (after the first user message) when a
session starts. Contains session-level metadata and intelligence
context.

Fields:
  type                    "conversation_metadata"
  sessionId               string    session identifier
  startTime               string    ISO 8601 session start time
  endTime                 null      always null at creation
  conversation_context    object    session state
    active_plugins        array     list of plugin names
    session_goals         array     always empty at creation
    conversation_summary  string    always empty at creation
  provider                string    LLM provider type (openai, anthropic, custom, etc)
  kollabor_intelligence   object    intelligence metadata
    conversation_memory   object
      related_sessions    array     session IDs from same day
      recurring_themes    array     always empty at creation
      user_patterns       array     learned user behavior patterns

Example:
  {
    "type": "conversation_metadata",
    "sessionId": "2604201816-omega-wave",
    "startTime": "2026-04-20T18:16:18.409136Z",
    "endTime": null,
    "uuid": "117a7667-...",
    "timestamp": "2026-04-20T18:16:19.967526Z",
    "cwd": "/Users/example/dev/kollab",
    "gitBranch": "main",
    "version": "0.5.7",
    "provider": "custom",
    "conversation_context": {
      "active_plugins": [],
      "session_goals": [],
      "conversation_summary": ""
    },
    "kollabor_intelligence": {
      "conversation_memory": {
        "related_sessions": [],
        "recurring_themes": [],
        "user_patterns": ["asks_clarifying_questions"]
      }
    }
  }


Entry Type: user
=================

Written for each user input message. Includes intelligence analysis
of the message content.

Fields:
  type                    "user"
  message                 object
    role                  string    always "user"
    content               string    raw user input text
  kollabor_intelligence   object
    user_context          object
      type                string    "system_initialization" or absent
      message_length      number    character count
      has_code            boolean   true if content contains triple backticks
      has_question        boolean   true if content contains "?"
      has_command         boolean   true if command verbs detected
      detected_intent     string    one of: debugging, feature_development,
                                    refactoring, seeking_help, testing,
                                    general_conversation
      project_context_loaded  boolean  (for system_initialization only)
    session_context       object
      conversation_phase  string    initiation, exploration, development, deep_work
      message_count       number    total messages so far
      session_duration    number    seconds since session start
      recurring_themes    array     deduplicated recent intent tags
      active_files        array     last 5 tracked file paths
    project_awareness     object
      project_type        string    e.g. "python_terminal_app"
      architecture        string    e.g. "plugin_based"
      recent_changes      array     always empty in current impl
      known_issues        array     always empty in current impl
      coding_standards    object    always empty in current impl

Example:
  {
    "type": "user",
    "parentUuid": "f096cd4c-...",
    "isSidechain": false,
    "userType": "external",
    "cwd": "/Users/example/dev/kollab",
    "sessionId": "2604201816-omega-wave",
    "version": "0.5.7",
    "gitBranch": "main",
    "message": {
      "role": "user",
      "content": "standby. this is a notification test."
    },
    "uuid": "ae28d403-...",
    "timestamp": "2026-04-20T18:16:20.130907Z",
    "kollabor_intelligence": {
      "user_context": {
        "message_length": 85,
        "has_code": false,
        "has_question": false,
        "has_command": false,
        "detected_intent": "testing"
      },
      "session_context": {
        "conversation_phase": "exploration",
        "message_count": 3,
        "session_duration": 1.722616,
        "recurring_themes": [],
        "active_files": []
      },
      "project_awareness": {
        "project_type": "python_terminal_app",
        "architecture": "plugin_based",
        "recent_changes": [],
        "known_issues": [],
        "coding_standards": {}
      }
    }
  }


Entry Type: assistant
======================

Written for each LLM response. The message.content field uses an array
format supporting both text and tool_use content blocks.

Fields:
  type                    "assistant"
  provider                string    LLM provider (openai, anthropic, custom, etc)
  requestId               string    "req_kollabor_{unix_timestamp}"
  message                 object
    id                    string    "msg_kollabor_{unix_timestamp}"
    type                  string    always "message"
    role                  string    always "assistant"
    model                 string    actual model name or provider fallback
    content               array     ordered list of content blocks
      [{type:"text",      text: string}]         text content
      [{type:"tool_use",  id/name/input: obj}]   tool call
    stop_reason           string|null  "tool_use" if tool calls present, else null
    stop_sequence         string|null  always null
    usage                 object    token usage statistics (or empty object)
  kollabor_intelligence   object    (optional, only when thinking present)
    thinking              array     list of thinking content strings
    has_thinking          boolean   whether thinking content exists
    thinking_duration     number|null  seconds spent in thinking phase

Content Block Types:

  text:
    {
      "type": "text",
      "text": "response text here"
    }

  tool_use:
    {
      "type": "tool_use",
      "id": "toolu_xyz123",
      "name": "terminal",
      "input": {"command": "git status"}
    }

Example:
  {
    "type": "assistant",
    "parentUuid": "ae28d403-...",
    "isSidechain": false,
    "userType": "external",
    "cwd": "/Users/example/dev/kollab",
    "sessionId": "2604201816-omega-wave",
    "version": "0.5.7",
    "gitBranch": "main",
    "provider": "custom",
    "message": {
      "id": "msg_kollabor_1745167000",
      "type": "message",
      "role": "assistant",
      "model": "claude-sonnet-4-20250514",
      "content": [
        {"type": "text", "text": "on it. let me check current state."},
        {"type": "tool_use", "id": "toolu_abc123", "name": "terminal",
         "input": {"command": "git status"}}
      ],
      "stop_reason": "tool_use",
      "stop_sequence": null,
      "usage": {"input_tokens": 1500, "output_tokens": 45}
    },
    "requestId": "req_kollabor_1745167000",
    "uuid": "b3f7e2a1-...",
    "timestamp": "2026-04-20T18:16:21.500000Z"
  }


Entry Type: system
===================

System entries capture everything that is not a direct user or LLM
message: tool results, hook output, hub relay messages, compaction
events, and other infrastructure signals.

Fields:
  type                    "system"
  subtype                 string    categorizes the system event
  content                 string    event body (plain text or structured)
  isMeta                  boolean   always false in current impl
  level                   string    always "info"
  toolUseID               string    (optional) links to a tool_use content block

Subtypes:

  informational     default subtype for tool results, hook output
  hub_rebirth       vault/context rehydration on agent startup
  hub_incoming      relayed message from another hub agent
  hub_nudge         system nudges (e.g. "other agents working")
  crystal_nudge     vault memory nudge surfaced by keyword match
  wake_header       injected on agent wake from idle state
  skill_activation  skill loaded during session
  context_snapshot  context state captured before compaction

Tool results use the informational subtype with a toolUseID linking
them back to the tool_use block in the parent assistant entry.

Hub messages (hub_rebirth, hub_incoming) contain XML-formatted
content that the hub plugin injects into the conversation stream.

Example (tool result):
  {
    "type": "system",
    "parentUuid": "b3f7e2a1-...",
    "isSidechain": false,
    "userType": "external",
    "cwd": "/Users/example/dev/kollab",
    "sessionId": "2604201816-omega-wave",
    "version": "0.5.7",
    "gitBranch": "main",
    "subtype": "informational",
    "content": "Tool result: [terminal]\nOn branch main\n...",
    "isMeta": false,
    "timestamp": "2026-04-20T18:16:22.100000Z",
    "uuid": "c4d8f3b2-...",
    "level": "info",
    "toolUseID": "toolu_abc123"
  }

Example (hub incoming):
  {
    "type": "system",
    "subtype": "hub_incoming",
    "content": "[hub channel: koordinator -> lapis]\nnotification test",
    ...
  }


Entry Type: conversation_end
=============================

Written when a session terminates. Contains aggregate statistics.

Fields:
  type                    "conversation_end"
  sessionId               string    session identifier
  endTime                 string    ISO 8601 session end time
  summary                 object
    total_messages        number    count of all entries written
    duration              number    seconds from start to end
    themes                array     deduplicated intent tags from session
    files_modified        array     tracked file paths interacted with

Example:
  {
    "type": "conversation_end",
    "sessionId": "2604201816-omega-wave",
    "endTime": "2026-04-20T19:30:00.000000Z",
    "uuid": "e5a9c4d3-...",
    "timestamp": "2026-04-20T19:30:00.000000Z",
    "summary": {
      "total_messages": 42,
      "duration": 6838.5,
      "themes": ["debugging", "feature_development"],
      "files_modified": ["src/main.py", "docs/schema.md"]
    }
  }


Threading Model
================

Entries form a tree via parentUuid:

  conversation_metadata (no parent)
    |
    +-- user (parentUuid = conversation_metadata.uuid)
    |     |
    |     +-- assistant (parentUuid = user.uuid)
    |           |
    |           +-- system/tool_result (parentUuid = assistant.uuid)
    |
    +-- user (parentUuid = previous user.uuid)
          |
          +-- ...

The first user entry has parentUuid = null (it is the root). The
conversation_metadata entry follows it, also with null parent. After
that, entries chain: user -> assistant -> system (tool result), then
the next user message chains from the previous user or assistant.


File Lifecycle
===============

  [1] Session starts: file created on first write
  [2] Messages appended chronologically
  [3] conversation_metadata written after first user message
  [4] conversation_end written on session shutdown
  [5] File persists indefinitely under conversations/

The conversation_memory subdirectory stores learned patterns:
  conversations/memory/user_patterns.json
  conversations/memory/project_context.json
  conversations/memory/solution_history.json

These are loaded on session start and updated after each message.


Session Resume
===============

Sessions can be resumed via /resume. The session_parser.py module
reads the JSONL file and extracts:
  - session_id, start_time, end_time
  - working_directory, git_branch
  - message_count, turn_count
  - preview_messages (first 3 user/assistant messages)
  - topics (from conversation_end summary)


Intelligence System
====================

The kollabor_intelligence field on user and assistant entries powers
several learning features:

  User Pattern Detection:
    - asks_clarifying_questions   "?" in content
    - prefers_direct_commands     command verbs detected
    - provides_detailed_context   messages > 200 chars
    - shares_code_frequently      triple backticks in content

  Intent Classification:
    - debugging              fix, bug, error, broken
    - feature_development    create, new, add, implement
    - refactoring            refactor, clean, improve, optimize
    - seeking_help           help, how, what, explain
    - testing                test, check, verify
    - general_conversation   (fallback)

  Conversation Phase:
    - initiation      message_count < 2
    - exploration     message_count < 10
    - development     message_count < 30
    - deep_work       message_count >= 30

  Solution Pattern Tracking (assistant):
    - uses_terminal_commands
    - uses_mcp_tools
    - provides_code_examples
    - provides_detailed_explanations
    - explains_reasoning

These are persisted in the memory/ directory and carried across
sessions.


Quick Reference
================

Count entries by type:
  cat session.jsonl | python3 -c "
  import sys, json
  from collections import Counter
  counts = Counter()
  for line in sys.stdin:
      if line.strip():
          data = json.loads(line)
          t = data.get('type','?')
          st = data.get('subtype','')
          counts[f'{t}/{st}' if st else t] += 1
  for k, v in counts.most_common():
      print(f'{v:4d} {k}')
  "

Extract all user messages:
  cat session.jsonl | python3 -c "
  import sys, json
  for line in sys.stdin:
      data = json.loads(line.strip())
      if data.get('type') == 'user':
          print(data['message']['content'][:120])
  "

Get session metadata:
  cat session.jsonl | python3 -c "
  import sys, json
  for line in sys.stdin:
      data = json.loads(line.strip())
      if data.get('type') == 'conversation_metadata':
          print(f'Session: {data[\"sessionId\"]}')
          print(f'Started: {data[\"startTime\"]}')
          print(f'Provider: {data[\"provider\"]}')
          print(f'Version: {data[\"version\"]}')
          break
  "

Find tool calls:
  cat session.jsonl | python3 -c "
  import sys, json
  for line in sys.stdin:
      data = json.loads(line.strip())
      if data.get('type') == 'assistant':
          for block in data.get('message',{}).get('content',[]):
              if block.get('type') == 'tool_use':
                  print(f'  {block[\"name\"]}({json.dumps(block[\"input\"])[:80]})')
  "
