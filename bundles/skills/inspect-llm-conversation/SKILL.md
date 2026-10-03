---
name: inspect-llm-conversation
description: "Inspect and debug LLM conversation history and message flow"
---

Inspect and debug LLM conversation history and message flow

skill name: inspect-llm-conversation

purpose:
  examine conversation state, message history, and queue status to diagnose
  conversation flow issues in kollab

when to use:
  - messages not appearing in conversation history
  - tools not executing after user response
  - need to verify message threading or parent uuids
  - debugging context window or history truncation
  - investigating duplicate or missing messages

methodology:
  1. check current session state (session id, message count, queue status)
  2. inspect conversation history in memory
  3. check processing queue state
  4. review persisted conversation logs if needed
  5. trace message flow from user input through processing

tools and commands:

  files to read:
    - kollabor/llm/llm_service.py
      conversation state: conversation_history, conversation_manager
      queue: processing_queue, is_processing, turn_completed

    - kollabor/llm/conversation_manager.py
      session tracking: current_session_id, messages list, message_index
      context: context_window (the whole conversation, never trimmed by count)
      storage: conversations_dir

    - kollabor/llm/response_parser.py
      parsing: parse_response()
      tool extraction: tool_call_pattern, terminal_pattern

  terminal commands:
    python3 -c "
import sys
sys.path.insert(0, '.')
from kollabor.config.loader import ConfigLoader
from kollabor.llm.conversation_manager import ConversationManager
config = ConfigLoader().load()
cm = ConversationManager(config)
print('Session:', cm.current_session_id)
print('Messages:', len(cm.messages))
print('Context window:', len(cm.context_window))
"

    python3 -c "
import sys, json
sys.path.insert(0, '.')
from kollabor.storage.state_manager import StateManager
sm = StateManager()
history = sm.get('llm.conversation_history', [])
print('Total messages in state:', len(history))
for i, msg in enumerate(history[-5:]):
    print(f'  [{i}] {msg.get(\"role\", \"unknown\")}: {msg.get(\"content\", \"\")[:50]}...')
"

    ls -la ~/.kollab/conversations/
    ls -la .kollab/conversations/

  grep patterns for debugging:
    grep -r "add_message\|log_user_message\|log_assistant_message" kollabor/llm/

example workflow:

  scenario: assistant reply or tool results missing from history after a turn

  1. check conversation manager for message threading:
     read kollabor/llm/conversation_manager.py lines 66-123
     look for: add_message method, parent_uuid handling

  2. view raw conversation logs:
     terminal: tail -20 ~/.kollab/conversations/*.jsonl
     terminal: jq . ~/.kollab/conversations/session_*.json 2>/dev/null | tail -50

expected output:

  [ok] session state
    session_id: frost-blade-1234
    messages in memory: 42
    context window: 42 / 90
    queue size: 0 / 10

  [ok] message threading
    current_parent_uuid: abc-123-def
    last message role: assistant
    thread depth: 3

troubleshooting tips:

  issue: messages not appearing in history
    - verify _add_conversation_message is being called (llm_service.py:38)
    - check conversation_manager.add_message is syncing (llm_service.py:66-71)
    - look for exceptions in conversation_logger.log_user_message

  issue: tools not executing
    - check tool_executor.execute_all_tools is being called
    - look for exceptions during tool execution in logs
    - note: xml tools always run; a <question> tag in a reply is plain text and suspends nothing

  issue: context window truncation
    - nothing trims by message count: check compaction (plugins/context_compaction_plugin.py)
    - verify conversation_manager._update_context_window
    - look for system message being dropped from context

  issue: duplicate messages
    - check message flow through message_display.display_user_message
    - verify message_coordinator is not double-displaying
    - look for multiple display_message_sequence calls
