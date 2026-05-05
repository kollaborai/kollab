---
name: inspect-llm-conversation
description: "Inspect and debug LLM conversation history and message flow"
---

Inspect and debug LLM conversation history and message flow

skill name: inspect-llm-conversation

purpose:
  examine conversation state, message history, pending tools queue, and
  question gate state to diagnose conversation flow issues in kollab

when to use:
  - messages not appearing in conversation history
  - question gate appears stuck (agent asks question but doesn't continue)
  - tools not executing after user response
  - need to verify message threading or parent uuids
  - debugging context window or history truncation
  - investigating duplicate or missing messages

methodology:
  1. check current session state (session id, message count, queue status)
  2. inspect conversation history in memory
  3. check pending tools queue and question gate state
  4. review persisted conversation logs if needed
  5. trace message flow from user input through processing

tools and commands:

  files to read:
    - kollabor/llm/llm_service.py
      conversation state: conversation_history, conversation_manager, pending_tools
      question gate: question_gate_active, question_gate_enabled
      queue: processing_queue, is_processing, turn_completed

    - kollabor/llm/conversation_manager.py
      session tracking: current_session_id, messages list, message_index
      context: context_window, max_history
      storage: conversations_dir

    - kollabor/llm/response_parser.py
      question detection: question_pattern, parse_response()
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
print('Context window:', len(cm.context_window), '/', cm.max_history)
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
    grep -r "question_gate_active" kollabor/llm/
    grep -r "pending_tools" kollabor/llm/
    grep -r "add_message\|log_user_message\|log_assistant_message" kollabor/llm/

example workflow:

  scenario: agent asked a question but isn't continuing after user response

  1. check question gate state:
     read kollabor/llm/llm_service.py lines 195-202
     look for: self.question_gate_active, self.pending_tools

  2. check response parser for question detection:
     read kollabor/llm/response_parser.py lines 439-443, 548-563
     look for: question_pattern regex, _extract_question method

  3. check conversation manager for message threading:
     read kollabor/llm/conversation_manager.py lines 66-123
     look for: add_message method, parent_uuid handling

  4. view raw conversation logs:
     terminal: tail -20 ~/.kollab/conversations/*.jsonl
     terminal: jq . ~/.kollab/conversations/session_*.json 2>/dev/null | tail -50

  5. trace question gate flow in llm_service:
     read kollabor/llm/llm_service.py lines 1068-1105, 1474-1481, 1667-1677
     look for: question gate handling in process_user_input and message processing

expected output:

  [ok] session state
    session_id: frost-blade-1234
    messages in memory: 42
    context window: 42 / 90
    queue size: 0 / 10

  [ok] question gate state
    question_gate_enabled: true
    question_gate_active: false
    pending_tools: 0

  [ok] message threading
    current_parent_uuid: abc-123-def
    last message role: assistant
    thread depth: 3

  or if issue detected:

  [error] question gate stuck
    question_gate_active: true (should be false)
    pending_tools: 2 tools suspended
    last assistant message contains: <question>...</question>
    recommendation: check question gate reset logic in process_user_input

troubleshooting tips:

  issue: messages not appearing in history
    - verify _add_conversation_message is being called (llm_service.py:38)
    - check conversation_manager.add_message is syncing (llm_service.py:66-71)
    - look for exceptions in conversation_logger.log_user_message

  issue: question gate not clearing
    - check llm_service.py lines 1103-1105 (question gate reset)
    - verify question_gate_enabled is true in config
    - check for <question> tag in last assistant response

  issue: pending tools not executing
    - verify pending_tools queue is populated (llm_service.py:200)
    - check tool_executor.execute_all_tools is being called
    - look for exceptions during tool execution in logs

  issue: context window truncation
    - check max_history config (default 90 in llm_service.py:120)
    - verify conversation_manager._update_context_window (lines 138-148)
    - look for system message being dropped from context

  issue: duplicate messages
    - check message flow through message_display.display_user_message
    - verify message_coordinator is not double-displaying
    - look for multiple display_message_sequence calls
