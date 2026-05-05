
Conversation Pipeline Wiring Reference
=======================================

Full trace of how a message flows through the kollabor system, from
user keystroke to JSONL write. Covers every component involved,
their wiring, and known gaps.


Architecture Overview
=====================

The conversation system has four layers:

  [1] Event Bus (kollabor-events)     hooks, priorities, routing
  [2] LLM Coordinator (kollabor/llm)  orchestration, session, queue
  [3] Queue Processor (kollabor-agent) LLM turns, tool execution
  [4] Conversation Logger (kollabor-ai) JSONL persistence

Data flows down: event -> coordinator -> queue processor -> logger.
System messages can inject at any layer via inject_system_message().


Components
==========

LLMService (kollabor/llm/llm_coordinator.py, ~1200 lines)
  Central orchestrator. Owns:
    - conversation_history (list of ConversationMessage)
    - conversation_logger (KollaborConversationLogger)
    - conversation_manager (ConversationManager)
    - api_service (APICommunicationService)
    - tool_executor (ToolExecutor)
    - queue_processor (QueueProcessor)
    - message_handler (MessageHandler)
    - session_manager (SessionManager)
    - streaming_handler (StreamingHandler)
    - hook_system (LLMHookSystem)

  Key methods:
    - process_user_input()     main entry point for user messages
    - inject_system_message()  inject into history + JSONL
    - inject_tool_grant()      notify agent of new tool
    - inject_tool_revoke()     notify agent of removed tool
    - shutdown()               log conversation_end + cleanup

MessageHandler (kollabor/llm/message_handler.py)
  Event bus callbacks. Registered on USER_INPUT, CANCEL_REQUEST,
  ADD_MESSAGE, TRIGGER_LLM_CONTINUE, CONTEXT_INJECTION hooks.
  Delegates everything to LLMService.

SessionManager (kollabor/llm/session_manager.py, ~204 lines)
  Session lifecycle:
    - initialize_conversation()  build system prompt, log first message
    - restart_session()          end current, start fresh
    - set_conversation_context() set version/plugins/provider on logger

QueueProcessor (packages/kollabor-agent/src/kollabor_agent/queue_processor.py, ~1286 lines)
  LLM turn execution:
    - _process_queue()           drain message queue
    - _execute_llm_turn()        single LLM API call + response handling
    - _continue_conversation()   agentic continuation (tool result -> next turn)
    - _process_message_batch()   batch processing variant

  Owns:
    - message queue with overflow strategy
    - turn loop (continues until no tools or stop signal)
    - tool execution and result logging

KollaborConversationLogger (packages/kollabor-ai/src/kollabor_ai/conversation_logger.py, ~890 lines)
  JSONL persistence. Five write methods:
    - log_conversation_start()   session metadata entry
    - log_user_message()         user input with intelligence analysis
    - log_assistant_message()    LLM response with tool_use blocks
    - log_system_message()       tool results, hub events, injections
    - log_conversation_end()     session termination summary

  All writes go through _append_to_jsonl() which appends one JSON
  line to the session file.

SessionParser (packages/kollabor-ai/src/kollabor_ai/session_parser.py, ~125 lines)
  Read path for resume. Parses JSONL to extract metadata:
  session_id, start_time, end_time, message_count, turn_count,
  topics, working_directory, git_branch, preview_messages.


Pipeline Flow: Session Startup
===============================

Triggered once when kollabor starts.

  Application.__init__()
    |
    +-- creates KollaborConversationLogger(conversations_dir)
    +-- creates ConversationManager()
    +-- creates SessionManager(logger, manager, config, ...)
    |
    v
  LLMService._initialize_llm()
    |
    +-- SessionManager.set_conversation_context()
    |     sets app_version, active_plugins, provider on logger
    |
    +-- SessionManager.initialize_conversation(conversation_history)
    |     |
    |     +-- prompt_builder.build()  ->  system prompt string
    |     +-- conversation_history.append(system_message)
    |     +-- logger.log_user_message(system_prompt)
    |     |     writes JSONL: type="user" with system_initialization context
    |     |     returns: parent_uuid
    |     |
    |     +-- returns parent_uuid to coordinator
    |
    +-- logger.log_conversation_start()
    |     writes JSONL: type="conversation_metadata"
    |
    +-- start_task_monitor() (if enabled)
    |
    v
  System ready. Waiting for user input.

JSONL write order at startup:
  Line 1: user (system prompt, logged as user message)
  Line 2: conversation_metadata
  Line 3+: user messages, assistant responses, system events


Pipeline Flow: User Input
==========================

Triggered on every user keystroke (Enter).

  EventBus fires USER_INPUT event
    |
    v
  [hook: CONTEXT_INJECTION]  (priority: PREPROCESSING)
    MessageHandler.handle_context_injection()
      scans input for keyword triggers, injects context
    |
    v
  [hook: USER_INPUT]  (priority: LLM)
    MessageHandler.handle_user_input()
      gates on startup_ready (30s timeout)
      |
      v
    LLMService.process_user_input(message)
      |
      +-- [question gate check]
      |     if question_gate_active and pending_tools:
      |       execute pending tools first
      |       log_system_message(subtype="tool_call") for each result
      |       add tool results to conversation_history
      |
      +-- logger.log_user_message(message)
      |     writes JSONL: type="user" with intelligence analysis
      |     updates current_parent_uuid
      |
      +-- _enqueue_with_overflow_strategy(message)
      |     adds to message queue
      |
      +-- if not is_processing:
            create_background_task(_process_queue())
      |
      v
    QueueProcessor._process_queue()
      |
      v
    QueueProcessor._execute_llm_turn()
      (details below)


Pipeline Flow: LLM Turn Execution
==================================

This is the core processing loop inside QueueProcessor.

  _execute_llm_turn()
    |
    +-- api_service.call_llm(conversation_history, ...)
    |     returns: response, thinking_blocks, raw_tool_calls, usage
    |
    +-- response_parser.parse(response, raw_tool_calls)
    |     extracts: clean_response, all_tools, has_native_tools
    |
    +-- logger.log_assistant_message(
    |       clean_response,
    |       parent_uuid,
    |       model,
    |       usage_stats,
    |       thinking_content,
    |       tool_calls          <- array of {id, name, input}
    |   )
    |   writes JSONL: type="assistant" with content blocks
    |   updates parent_uuid
    |
    +-- conversation_history.append(assistant_message)
    |
    +-- [tool execution branch]
    |     |
    |     +-- if has_native_tools:
    |     |     execute native tool calls
    |     |     for each result:
    |     |       logger.log_system_message(subtype="tool_result", tool_use_id)
    |     |
    |     +-- if xml_tools:
    |           execute XML tool calls
    |           for each result:
    |             logger.log_system_message(subtype="tool_call")
    |
    +-- [continuation check]
          if tools were executed and more turns needed:
            _continue_conversation() -> _execute_llm_turn() (loop)
          else:
            turn_completed = True
            renderer shows final output

JSONL write order per turn:
  1. assistant  (LLM response with optional tool_use blocks)
  2. system     (one per tool result, subtype="tool_result" or "tool_call")
  ... repeat if agentic continuation ...


Pipeline Flow: System Message Injection
========================================

System messages are injected at multiple points:

  1. LLMService.inject_system_message(content, subtype)
     - appends to conversation_history as ConversationMessage(role="user")
     - calls logger.log_system_message() for JSONL persistence
     - used by: hub plugin, wake headers, crystal nudges, tool grants

  2. Hub Plugin (plugins/hub/plugin.py)
     inject_system_message() is called with subtypes:
       - hub_rebirth     agent startup vault/context rehydration
       - hub_incoming    relayed message from peer agent
       - hub_nudge       system nudges (e.g. "other agents working")
       - crystal_nudge   vault memory surfaced by keyword match
       - wake_header     injected on agent wake from idle state

  3. Tool Grants/Revokes
     inject_tool_grant(tool_name, reason)
       - renders tool documentation from registry
       - inject_system_message(content, subtype="tool_grant")
       - updates bundle scope on tool_executor
     inject_tool_revoke(tool_name, reason)
       - injects revocation notification
       - removes from bundle scope

  4. Context Compaction Plugin
     writes to vault stream (not directly to JSONL)
     the next LLM turn picks up the compacted context

  5. ADD_MESSAGE Event
     plugins can fire ADD_MESSAGE to inject arbitrary messages
     MessageHandler.handle_add_message() processes them:
       - adds to conversation_history
       - logs via logger.log_user_message() or log_assistant_message()
       - optionally triggers LLM response


Pipeline Flow: Tool Result Logging
===================================

Tool results are logged as system entries. There are two paths:

  Native Tool Calls (OpenAI-style function calling):
    QueueProcessor processes each tool result:
      logger.log_system_message(
          f"Executed {tool_type} ({tool_id}): {output}",
          parent_uuid=parent_uuid,
          subtype="tool_result",
          tool_use_id=tool_id       <- links back to tool_use block
      )

  XML Tool Calls (parsed from LLM text output):
    QueueProcessor processes each tool result:
      logger.log_system_message(
          f"Executed {tool_type} ({tool_id}): {output}",
          parent_uuid=parent_uuid,
          subtype="tool_call",      <- NOTE: different subtype
      )

  [GAP] The XML path uses subtype="tool_call" while the native path
  uses subtype="tool_result". The XML path also omits tool_use_id.
  This is an inconsistency — both paths produce the same kind of
  entry (tool execution result) but with different subtype labels.

  Question Gate Tool Execution (llm_coordinator.py):
    When tools are executed during question gate resolution:
      logger.log_system_message(
          f"Executed {tool_type} ({tool_id}): {output}",
          parent_uuid=current_parent_uuid,
          subtype="tool_call",      <- same as XML path
      )


Pipeline Flow: Session Restart
===============================

Triggered by /clear or programmatic restart.

  SessionManager.restart_session()
    |
    +-- logger.log_conversation_end()
    |     writes JSONL: type="conversation_end" with summary
    |
    +-- conversation_manager.save_conversation() (if enabled)
    |
    +-- generate new session_id
    |
    +-- logger.reset_session(new_session_id)
    |     clears: message_count, start_time, thread_uuid
    |     creates new session file path
    |
    +-- conversation_manager.reset_session(new_session_id)
    |
    +-- api_service.set_session_id(new_session_id)
    |
    +-- conversation_history.clear()
    |   prompt_builder.build() -> new system prompt
    |   conversation_history.append(system_message)
    |
    +-- logger.log_conversation_start()
    |     writes JSONL: type="conversation_metadata" (new session file)
    |
    v
  Fresh session ready.


Pipeline Flow: Session End
===========================

Triggered on application shutdown.

  LLMService.shutdown()
    |
    +-- logger.log_conversation_end()
    |     writes JSONL: type="conversation_end" with summary
    |     summary includes: total_messages, duration, themes,
    |     files_modified
    |
    +-- cancel_all_background_tasks()
    |
    +-- stop_task_monitor()
    |
    v
  Session complete. JSONL file persists.


Threading and Async Model
==========================

  conversation_history    shared list, accessed from event loop only
  message queue           inside QueueProcessor, drained synchronously
  _execute_llm_turn()     single coroutine, no concurrent LLM calls
  inject_system_message() safe to call from any async context
  background tasks        managed by BackgroundTaskManager

  The queue ensures messages are processed sequentially even if
  the user types faster than the LLM responds. Overflow strategy
  merges pending messages into a single LLM turn.


Complete Flow Diagram
======================

  [user types message]
         |
         v
  Event Bus: USER_INPUT
         |
         +-- [hook: CONTEXT_INJECTION]
         |     context_service.trigger_context_injection()
         |
         +-- [hook: USER_INPUT]
               MessageHandler.handle_user_input()
                  |
                  v
               LLMService.process_user_input(message)
                  |
                  +-- question gate check
                  +-- JSONL: user entry (log_user_message)
                  +-- enqueue message
                  +-- start _process_queue()
                  |
                  v
               QueueProcessor._process_queue()
                  |
                  v
               QueueProcessor._execute_llm_turn()
                  |
                  +-- API call (api_service.call_llm)
                  +-- parse response
                  +-- JSONL: assistant entry (log_assistant_message)
                  +-- conversation_history.append
                  +-- execute tools
                  |     |
                  |     +-- JSONL: system/tool_result entries
                  |     +-- conversation_history.append (tool results)
                  |
                  +-- if tools executed and continuation needed:
                  |     loop back to _execute_llm_turn()
                  |
                  +-- else:
                        turn complete, render output
                        |
                        v
                  [output displayed to user]


  [system message injection]
         |
         v
  LLMService.inject_system_message(content, subtype)
         |
         +-- conversation_history.append(role="user", content)
         +-- JSONL: system entry (log_system_message)
         |
         v
  [available in next LLM turn]


  [session restart (/clear)]
         |
         v
  SessionManager.restart_session()
         |
         +-- JSONL: conversation_end
         +-- reset logger (new session file)
         +-- clear history
         +-- JSONL: user (new system prompt)
         +-- JSONL: conversation_metadata
         |
         v
  [fresh session ready]


Gaps and Inconsistencies
=========================

  [1] tool_result vs tool_call subtype inconsistency
      Native tool results use subtype="tool_result" with tool_use_id.
      XML tool results use subtype="tool_call" without tool_use_id.
      Question gate tools also use subtype="tool_call".
      These all represent the same concept (tool execution result)
      but use different subtypes and different field coverage.

  [2] System prompt logged as user message
      SessionManager.initialize_conversation() logs the system prompt
      via log_user_message() with a user_context override. This means
      the first JSONL "user" entry is actually the system prompt,
      not real user input. The type field is "user" but the intent
      is system initialization.

  [3] conversation_metadata written after first user message
      The conversation_metadata entry is written after the first user
      message (system prompt), not before. This means the first line
      of a JSONL file is a user entry, not the metadata entry. The
      session_parser handles this correctly but it's unintuitive.

  [4] No conversation_metadata update on provider change
      If the user switches providers mid-session (/profile), the
      conversation_metadata is not updated. The provider field in
      the metadata entry reflects the initial provider only.

  [5] inject_system_message uses role="user" in history
      When injecting system messages, the conversation_history gets
      a ConversationMessage with role="user" (not "system"). This
      makes the injected content visible to the LLM as user input,
      which is intentional (the LLM needs to see it) but could
      confuse tooling that expects role="system" for system content.

  [6] XML tool results lack tool_use_id
      The XML tool path does not pass tool_use_id to
      log_system_message(). This means XML tool results cannot be
      linked back to specific tool invocations in the JSONL.

  [7] Duplicate conversation_end entries possible
      If shutdown() is called after restart_session() has already
      logged a conversation_end, and the new session also gets a
      conversation_end on its own shutdown, the file contains two
      conversation_end entries. The session_parser handles this by
      taking the last one, but it's not clean.

  [8] file_interactions tracking is incomplete
      The conversation_logger tracks file_interactions but nothing
      in the pipeline actually populates it. The conversation_end
      summary.files_modified field will always be empty.


Source File Index
==================

  kollabor/llm/llm_coordinator.py
    Central orchestrator, owns all components
    process_user_input() at line ~937
    inject_system_message() at line ~104
    shutdown() at line ~1192

  kollabor/llm/message_handler.py
    Event bus callbacks, delegates to coordinator
    handle_user_input() at line ~87
    handle_add_message() at line ~180

  kollabor/llm/session_manager.py (~204 lines)
    Session lifecycle management
    initialize_conversation() at line ~56
    restart_session() at line ~105

  packages/kollabor-agent/src/kollabor_agent/queue_processor.py (~1286 lines)
    LLM turn execution and tool processing
    _execute_llm_turn() at line ~850 (approx)
    _process_queue() at line ~500 (approx)

  packages/kollabor-ai/src/kollabor_ai/conversation_logger.py (~890 lines)
    JSONL persistence, all write operations
    log_user_message() at line ~446
    log_assistant_message() at line ~490
    log_system_message() at line ~575 (approx)
    log_conversation_start() at line ~410 (approx)
    log_conversation_end() at line ~610 (approx)

  packages/kollabor-ai/src/kollabor_ai/session_parser.py (~125 lines)
    JSONL read path for session resume
    parse_session_jsonl() at line ~18

  plugins/hub/plugin.py
    Hub system message injection
    inject_system_message() calls with hub_rebirth, hub_incoming,
    hub_nudge, crystal_nudge, wake_header subtypes
