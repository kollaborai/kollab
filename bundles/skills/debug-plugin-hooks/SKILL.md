---
name: debug-plugin-hooks
description: "Debug plugin hook execution and trace event flow"
---

skill name: debug-plugin-hooks

purpose:
  trace plugin hook execution and debug event flow issues.
  helps identify which hooks are registered, their execution order,
  and diagnose hook failures in the kollabor event system.


when to use:
  [ ] hooks not executing as expected
  [ ] need to see all hooks for an event type
  [ ] tracking down hook failures or timeouts
  [ ] understanding event flow through pre/main/post phases
  [ ] verifying hook priority and execution order
  [ ] debugging why plugin hooks are not being called


methodology:

phase 1: event system discovery
  understand the event bus architecture
  identify registered hooks
  check hook status and metadata

phase 2: hook tracing
  trace execution flow for specific events
  inspect hook results and errors
  analyze performance metrics

phase 3: debugging execution issues
  identify why hooks are not executing
  find priority conflicts
  diagnose timeout and error conditions


tools and commands:

core files to read:
  <read><file>kollabor/events/bus.py</file></read>
  <read><file>kollabor/events/registry.py</file></read>
  <read><file>kollabor/events/executor.py</file></read>
  <read><file>kollabor/events/processor.py</file></read>
  <read><file>kollabor/events/models.py</file></read>

plugin examples:
  <read><file>plugins/hook_monitoring_plugin.py</file></read>
  <read><file>plugins/enhanced_input_plugin.py</file></read>

grep patterns for hook discovery:
  <terminal>grep -r "class.*Hook" kollabor/events/</terminal>
  <terminal>grep -r "EventType\." kollabor/events/</terminal>
  <terminal>grep -r "register_hook" plugins/</terminal>
  <terminal>grep -r "async def.*hook\|def.*_hook" plugins/ --include="*.py"</terminal>

find all hook registrations:
  <terminal>grep -r "Hook(" plugins/ --include="*.py" -A 5</terminal>

check event type definitions:
  <terminal>grep -A 100 "class EventType" kollabor/events/models.py</terminal>

view hook status in logs:
  <terminal>grep -i "hook" .kollab/logs/kollab.log | tail -50</terminal>

check hook execution errors:
  <terminal>grep -i "hook.*error\|hook.*failed\|hook.*timeout" .kollab/logs/kollab.log</terminal>

check plugin initialization:
  <terminal>grep -i "plugin.*initialize\|register.*hook" .kollab/logs/kollab.log | tail -30</terminal>


event system architecture:

eventbus (kollabor/events/bus.py)
  central coordinator combining:
    - hookregistry: manages hook registration and lookup
    - hookexecutor: executes individual hooks with error handling
    - eventprocessor: coordinates pre/main/post phase processing

key methods:
  - register_hook(hook): register a new hook
  - emit_with_hooks(event_type, data, source): process event through hooks
  - get_hook_status(): get all hook statuses
  - get_hooks_for_event(event_type): count hooks for event type
  - enable_hook(plugin_name, hook_name): enable a specific hook
  - disable_hook(plugin_name, hook_name): disable a specific hook


hookregistry (kollabor/events/registry.py)
  organizes hooks by event_type and priority
  tracks hook status (pending, working, completed, failed, timeout)

key methods:
  - get_hooks_for_event(event_type): get hooks sorted by priority
  - get_hook_status_summary(): get all hook statuses
  - get_registry_stats(): comprehensive statistics
  - enable_hook/disable_hook: toggle hooks


hookexecutor (kollabor/events/executor.py)
  executes individual hooks with timeout and error handling

key methods:
  - execute_hook(hook, event): execute single hook
  - get_execution_stats(results): get execution statistics


eventprocessor (kollabor/events/processor.py)
  coordinates pre/main/post phase processing

key methods:
  - process_event_with_phases(): process event through all phases
  - get_supported_event_types(): show pre/post mappings


event types (kollabor/events/models.py)

user input events:
  USER_INPUT_PRE, USER_INPUT, USER_INPUT_POST
  KEY_PRESS_PRE, KEY_PRESS, KEY_PRESS_POST
  PASTE_DETECTED

llm events:
  LLM_REQUEST_PRE, LLM_REQUEST, LLM_REQUEST_POST
  LLM_RESPONSE_PRE, LLM_RESPONSE, LLM_RESPONSE_POST
  LLM_THINKING, CANCEL_REQUEST

tool events:
  TOOL_CALL_PRE, TOOL_CALL, TOOL_CALL_POST

system events:
  SYSTEM_STARTUP, SYSTEM_SHUTDOWN, RENDER_FRAME

rendering events:
  INPUT_RENDER_PRE, INPUT_RENDER, INPUT_RENDER_POST

command events:
  COMMAND_MENU_SHOW, COMMAND_MENU_NAVIGATE, COMMAND_MENU_SELECT
  SLASH_COMMAND_DETECTED, SLASH_COMMAND_EXECUTE

modal events:
  MODAL_TRIGGER, MODAL_SHOW, MODAL_HIDE


hook priority levels (from kollabor/events/models.py):
  SYSTEM = 1000    (highest priority, executes first)
  SECURITY = 900
  PREPROCESSING = 500
  LLM = 100
  POSTPROCESSING = 50
  DISPLAY = 10     (lowest priority, executes last)


phase 1: discover registered hooks

step 1: list all registered hooks via hook monitoring plugin

check if hook monitoring plugin is enabled:
  <read><file>.kollab/config.json</file></read>

look for:
  {
    "plugins": {
      "hook_monitoring": {
        "enabled": true
      }
    }
  }

if disabled, enable in config.json and restart.


step 2: query hook registry from event bus

add temporary debug endpoint in kollabor/application.py:

  async def debug_list_hooks(self):
      """list all registered hooks for debugging."""
      status = self.event_bus.get_hook_status()
      stats = self.event_bus.get_registry_stats()
      return {
          "summary": status,
          "stats": stats
      }

call from interactive session or add debug logging.


step 3: search for hook registrations in plugins

find all hooks registered by a specific plugin:
  <terminal>grep -A 10 "def register_hooks" plugins/enhanced_input_plugin.py</terminal>

find all hook definitions:
  <terminal>grep -r "Hook(" plugins/ --include="*.py" | grep -v "^.*:#"</terminal>


step 4: verify plugin initialization

check plugin loading in logs:
  <terminal>grep -i "plugin.*load\|plugin.*init" .kollab/logs/kollab.log | tail -20</terminal>

check hook registration:
  <terminal>grep -i "registered hook" .kollab/logs/kollab.log | tail -30</terminal>


phase 2: trace hook execution

step 1: enable debug logging for hooks

in .kollab/config.json:
  {
    "plugins": {
      "hook_monitoring": {
        "debug_logging": true,
        "log_all_events": true,
        "log_event_data": true
      }
    }
  }

restart application to apply.


step 2: monitor hook execution in real-time

watch logs for hook execution:
  <terminal>tail -f .kollab/logs/kollab.log | grep -i "hook"</terminal>

filter for specific event type:
  <terminal>tail -f .kollab/logs/kollab.log | grep "USER_INPUT"</terminal>

watch for errors:
  <terminal>tail -f .kollab/logs/kollab.log | grep -i "error\|fail\|timeout"</terminal>


step 3: add custom logging to hooks

example: add logging to a hook callback

  async def my_hook_callback(self, data: dict, event: event) -> dict:
      logger.debug(f"[HOOK] {self.name}.my_hook: start")
      logger.debug(f"[HOOK] event type: {event.type.value}")
      logger.debug(f"[HOOK] data keys: {list(data.keys())}")

      try:
          result = await self._do_work(data)
          logger.debug(f"[HOOK] {self.name}.my_hook: complete")
          return result
      except exception as e:
          logger.error(f"[HOOK] {self.name}.my_hook: error - {e}")
          raise


step 4: trace execution order

enable hook monitoring plugin execution tracking:
  {
    "plugins": {
      "hook_monitoring": {
        "log_performance": true,
        "performance_threshold_ms": 10
      }
    }
  }

this logs each hook execution with timing.


phase 3: debugging execution issues

issue 1: hook not executing

checklist:
  [ ] is plugin loaded?
      <terminal>grep -i "plugin.*{plugin_name}" .kollab/logs/kollab.log</terminal>

  [ ] is hook registered?
      <terminal>grep -i "register.*hook.*{hook_name}" .kollab/logs/kollab.log</terminal>

  [ ] is hook enabled?
      check hook.enabled in code or via registry

  [ ] is event being emitted?
      add logging at event emission point

  [ ] is priority correct?
      higher priority hooks execute first
      check for hooks that might cancel event


issue 2: hook executing in wrong order

check priority values:
  <terminal>grep -r "priority=" plugins/ --include="*.py" -b 2</terminal>

remember: higher numbers execute first.
  system (1000) > security (900) > preprocessing (500) > llm (100) >
  postprocessing (50) > display (10)


issue 3: hook timing out

default timeout is 30 seconds.

symptoms:
  [ ] hook starts but never completes
  [ ] log shows "hook timed out after {timeout}s"

solutions:
  - increase hook timeout in hook definition
  - check for infinite loops
  - check for blocking operations (use async properly)
  - check for deadlock conditions


issue 4: hook erroring

symptoms:
  [ ] log shows exception traceback
  [ ] hook status shows "failed"

debug steps:
  1. find exception in logs
  2. check error_action setting (continue vs stop)
  3. verify hook callback signature matches expected
  4. check for missing attributes in data dict


issue 5: event cancellation

symptoms:
  [ ] later hooks not executing
  [ ] log shows "event cancelled"

causes:
  - hook with error_action="stop" encountered error
  - hook explicitly set event.cancelled = true

find which hook cancelled:
  <terminal>grep -b 5 "event cancelled" .kollab/logs/kollab.log</terminal>


example workflow:

scenario: "my custom hook is not executing"

step 1: verify plugin loaded
  <terminal>grep -i "myplugin" .kollab/logs/kollab.log</terminal>

  expected: "myplugin loaded" or "initializing myplugin"

  if missing:
    - check plugin is in plugins/ directory
    - check plugin has __init__.py
    - check plugin is referenced in config.json

step 2: verify hook registered
  <terminal>grep -i "myhook" .kollab/logs/kollab.log</terminal>

  expected: "registered hook: myplugin.myhook"

  if missing:
    - check register_hooks() method calls event_bus.register_hook()
    - check hook definition has correct event_type

step 3: check hook enabled
  add temporary logging:
    logger.info(f"hook enabled: {hook.enabled}, event_type: {hook.event_type}")

step 4: verify event is emitted
  add logging at event emission:
    logger.info(f"emitting event: {event_type.value}")

step 5: check priority conflicts
  maybe another hook with same priority is conflicting?

  <terminal>grep -r "priority=.*" plugins/ --include="*.py" | grep "{event_type}"</terminal>


example workflow 2:

scenario: "hook executes but errors out"

step 1: find the error
  <terminal>grep -a 20 "myhook.*error" .kollab/logs/kollab.log</terminal>

step 2: check hook signature
  must be: async def callback(self, data: dict, event: event) -> dict

step 3: add try/except in hook
  try:
      result = await self._process(data)
      return {"data": result}
  except exception as e:
      logger.exception("hook processing failed")
      raise

step 4: verify data dict contents
  logger.debug(f"received data: {data}")

  check expected keys exist before accessing.


example workflow 3:

scenario: "understanding pre/main/post flow"

step 1: check event type mappings
  <read><file>kollabor/events/processor.py</file><lines>33-42</lines></read>

  shows which events have pre/post phases.

step 2: trace event processing
  enable hook_monitoring debug logging:
    "log_all_events": true

step 3: observe execution order in logs
  you should see:
    [pre] hooks for user_input_pre
    [main] hooks for user_input
    [post] hooks for user_input_post

step 4: verify data flow
  pre hooks can modify data before main
  main hooks process the data
  post hooks see final data


advanced: programmatic hook inspection

create a debug script (debug_hooks.py):

  import asyncio
  import sys
  from pathlib import path

  sys.path.insert(0, str(path(__file__).parent))

  from kollabor.events.bus import eventbus
  from kollabor.events.models import eventtype

  async def main():
      bus = eventbus()

      # list all event types with hooks
      for event_type in eventtype:
          count = bus.get_hooks_for_event(event_type)
          if count > 0:
              print(f"{event_type.value}: {count} hooks")

      # get full status
      status = bus.get_hook_status()
      print(f"\ntotal hooks: {status['total_hooks']}")
      print(f"\nhook details:")
      for hook_key, details in status['hook_details'].items():
          print(f"  {hook_key}: {details}")

  asyncio.run(main())

run:
  <terminal>python debug_hooks.py</terminal>


troubleshooting tips:

tip 1: hooks not firing at all
  - verify plugin has register_hooks() method
  - verify register_hooks() is called during initialization
  - check for exceptions during registration
  - verify event_bus reference is not none

tip 2: intermittent hook execution
  - check for conditional logic in hook callback
  - verify hook.enabled is not being toggled
  - check for event cancellation by earlier hooks
  - verify no race conditions in async code

tip 3: performance issues
  - use hook_monitoring performance tracking
  - check for slow hooks via timing logs
  - profile hook callback functions
  - consider moving heavy work to background tasks

tip 4: debugging without logs
  - add print statements in hook callback
  - use python debugger: breakpoint() in hook
  - check event.data before and after hook
  - verify hook return value format

tip 5: testing hooks in isolation
  create test file:
    import asyncio
    from kollabor.events import event, eventtype, hook, hookpriority

    async def test_hook():
      # create test event
      event = event(
          type=eventtype.user_input,
          data={"input": "test"},
          source="test"
      )

      # create test hook
      hook = hook(
          name="test",
          plugin_name="test_plugin",
          event_type=eventtype.user_input,
          priority=hookpriority.llm.value,
          callback=lambda d, e: {"processed": true}
      )

      # execute
      result = await hook.callback(event.data, event)
      print(f"result: {result}")

    asyncio.run(test_hook())


expected output:

when this skill executes successfully, you should be able to:

  [ ] list all registered hooks for any event type
  [ ] trace execution order for an event flow
  [ ] identify which hooks failed or timed out
  [ ] understand pre/main/post phase processing
  [ ] debug why specific hooks are not executing
  [ ] measure hook execution performance
  [ ] inspect hook context and parameters


status tags reference:

  [ok]   hook is working correctly
  [warn] hook has issues but still executes
  [error] hook is failing or not executing
  [todo]  action needed to fix hook

common exit conditions:

  [ok]   issue identified and resolved
  [ok]   workaround implemented
  [warn] issue understood but not fixed
  [error] root cause unclear, needs more investigation
