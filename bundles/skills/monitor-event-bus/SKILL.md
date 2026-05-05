---
name: monitor-event-bus
description: "Monitor event bus activity in real-time"
---

skill name: monitor-event-bus

purpose:
  watch event bus activity in real-time to understand system behavior.
  helps visualize event flow, track active listeners, measure processing
  times, and diagnose performance bottlenecks in the event system.


when to use:
  [ ] need to see what events are firing in real-time
  [ ] investigating event propagation issues
  [ ] measuring event processing performance
  [ ] identifying which plugins are listening to which events
  [ ] debugging event-related race conditions
  [ ] understanding event flow for a specific user action


methodology:

phase 1: event bus discovery
  understand event bus architecture
  identify all registered event listeners
  check event type mappings

phase 2: real-time monitoring
  set up live event watching
  trace event propagation through pre/main/post phases
  capture event context and timing data

phase 3: analysis
  identify slow events/hooks
  find event bottlenecks
  visualize event flow patterns


tools and commands:

core files to read:
  <read><file>kollabor/events/bus.py</file></read>
  <read><file>kollabor/events/models.py</file></read>
  <read><file>kollabor/events/processor.py</file></read>
  <read><file>kollabor/events/registry.py</file></read>
  <read><file>kollabor/events/executor.py</file></read>

grep patterns for event discovery:
  <terminal>grep -r "emit_with_hooks\|emit(" kollabor/ --include="*.py"</terminal>
  <terminal>grep -r "EventType\." kollabor/ --include="*.py" | head -50</terminal>
  <terminal>grep -r "process_event_with_phases" kollabor/ --include="*.py"</terminal>

find all event emission points:
  <terminal>grep -r "await.*event_bus" kollabor/ --include="*.py" -B 2</terminal>

view event bus in logs:
  <terminal>grep -i "event.*emitted\|event.*processed" .kollab/logs/kollab.log | tail -50</terminal>

check event processing times:
  <terminal>grep -i "duration_ms\|processing.*time" .kollab/logs/kollab.log | tail -30</terminal>

monitor live events:
  <terminal>tail -f .kollab/logs/kollab.log | grep -i "event"</terminal>


event bus architecture:

eventbus (kollabor/events/bus.py)
  central coordinator for all event activity
  combines three specialized components:

key attributes:
  - hook_registry: manages hook registration and lookup
  - hook_executor: executes individual hooks with timeout/error handling
  - event_processor: coordinates pre/main/post phase processing

key methods for monitoring:
  - get_hook_status(): get current status of all registered hooks
  - get_registry_stats(): comprehensive statistics (counts, distributions)
  - get_hooks_for_event(event_type): count hooks for specific event
  - emit_with_hooks(event_type, data, source): main event emission point


eventprocessor (kollabor/events/processor.py)
  handles event flow through pre/main/post phases

pre/post event mappings:
  - USER_INPUT -> USER_INPUT_PRE + USER_INPUT_POST
  - KEY_PRESS -> KEY_PRESS_PRE + KEY_PRESS_POST
  - LLM_REQUEST -> LLM_REQUEST_PRE + LLM_REQUEST_POST
  - LLM_RESPONSE -> LLM_RESPONSE_PRE + LLM_RESPONSE_POST
  - TOOL_CALL -> TOOL_CALL_PRE + TOOL_CALL_POST

key methods:
  - process_event_with_phases(): main event processing pipeline
  - get_supported_event_types(): show all pre/post mappings


hookregistry (kollabor/events/registry.py)
  tracks all hooks by event type and priority

key methods:
  - get_hooks_for_event(event_type): get enabled hooks sorted by priority
  - get_hook_status_summary(): get all hook statuses with details
  - get_registry_stats(): comprehensive statistics

return data includes:
  - total_hooks: total number of registered hooks
  - hooks_per_event_type: count per event type
  - priority_distribution: hooks by priority level
  - hooks_per_plugin: hooks by plugin name


hookexecutor (kollabor/events/executor.py)
  executes hooks and tracks performance

key methods:
  - execute_hook(hook, event): execute single hook with timeout
  - get_execution_stats(results): calculate timing and success stats

return data includes:
  - successful: count of successful executions
  - failed: count of failed executions
  - timed_out: count of timeouts
  - total_duration_ms: total processing time
  - avg_duration_ms: average processing time


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
  STATUS_VIEW_CHANGED, STATUS_CONTENT_UPDATE
  PAUSE_RENDERING, RESUME_RENDERING

command events:
  SLASH_COMMAND_DETECTED, SLASH_COMMAND_EXECUTE, SLASH_COMMAND_COMPLETE
  COMMAND_MENU_SHOW, COMMAND_MENU_NAVIGATE, COMMAND_MENU_SELECT
  COMMAND_MENU_HIDE, COMMAND_MENU_FILTER

modal events:
  MODAL_TRIGGER, MODAL_SHOW, MODAL_HIDE, MODAL_SAVE
  STATUS_MODAL_TRIGGER, LIVE_MODAL_TRIGGER


phase 1: discover event bus state

step 1: query registry for hook statistics

check total hook count:
  <terminal>grep -i "total.*hook\|registered.*hook" .kollab/logs/kollab.log | tail -5</terminal>

check hooks per event type:
  <terminal>grep -i "hook.*for.*event\|event.*type" .kollab/logs/kollab.log | tail -20</terminal>

check plugin hook distribution:
  <terminal>grep -i "plugin.*hook" .kollab/logs/kollab.log | tail -20</terminal>


step 2: find all event emission points in code

search for emit_with_hooks calls:
  <terminal>grep -rn "emit_with_hooks" kollabor/ --include="*.py"</terminal>

sample output shows:
  - where events are emitted
  - which event types are used
  - what data is passed with events


step 3: identify active listeners for specific event

for a given event type (e.g., USER_INPUT):
  <terminal>grep -rn "USER_INPUT" plugins/ --include="*.py" | grep "Hook("</terminal>

this shows all hooks listening to USER_INPUT events.


phase 2: real-time event monitoring

step 1: enable event bus logging

check if debug logging is enabled:
  <read><file>.kollab/config.json</file></read>

look for logging configuration:
  {
    "logging": {
      "level": "DEBUG"
    }
  }

if not set, add and restart application.


step 2: monitor all events in real-time

watch all event emissions:
  <terminal>tail -f .kollab/logs/kollab.log | grep "event"</terminal>

filter for specific event type:
  <terminal>tail -f .kollab/logs/kollab.log | grep "USER_INPUT"</terminal>

filter for phase information:
  <terminal>tail -f .kollab/logs/kollab.log | grep "PRE\|MAIN\|POST"</terminal>

watch for event cancellations:
  <terminal>tail -f .kollab/logs/kollab.log | grep "cancel"</terminal>


step 3: create event monitoring helper

add this function to track events:

  # in kollabor/events/bus.py (for debugging)
  _event_log = []

  async def emit_with_hooks(self, event_type, data, source):
      entry = {
          "timestamp": datetime.now().isoformat(),
          "event_type": event_type.value,
          "source": source,
          "data_keys": list(data.keys()) if isinstance(data, dict) else str(type(data))
      }
      self._event_log.append(entry)
      logger.debug(f"[EVENT] {event_type.value} from {source}")
      return await self.event_processor.process_event_with_phases(event_type, data, source)

  def get_event_log(self, limit=100):
      return self._event_log[-limit:]


step 4: trace event flow through phases

enable verbose phase logging in processor:
  <terminal>grep -A 5 "process_event_with_phases" kollabor/events/processor.py</terminal>

look for phase logging:
  - "event {event_type} cancelled during pre phase"
  - "processing {phase_name} phase"

this shows the complete flow: pre -> main -> post


phase 3: performance measurement

step 1: measure event processing time

check hook execution times in logs:
  <terminal>grep -i "duration_ms\|executed.*hook" .kollab/logs/kollab.log | tail -50</terminal>

sort by slowest hooks:
  <terminal>grep -i "duration_ms" .kollab/logs/kollab.log | sort -t: -k2 -rn | head -20</terminal>


step 2: identify bottleneck hooks

find hooks taking longer than threshold:
  <terminal>awk '/duration_ms/ {if ($2 > 50) print}' .kollab/logs/kollab.log</terminal>

adjust threshold as needed (50ms in example).


step 3: create performance summary

run this command to aggregate timing:
  <terminal>grep "duration_ms" .kollab/logs/kollab.log | awk '{sum+=$2; count++} END {print "avg:", sum/count, "ms total:", sum, "ms count:", count}'</terminal>


step 4: trace slow event flows

when you find a slow event, trace its hooks:
  1. identify event type from logs
  2. find all hooks for that event type
  3. check each hook's callback implementation
  4. profile slow callbacks


example workflow:

scenario: "what events fire when i press enter?"

step 1: start real-time monitoring
  <terminal>tail -f .kollab/logs/kollab.log | grep -i "event\|hook"</terminal>

step 2: trigger the action
  press enter in the application

step 3: observe event sequence

typical sequence for enter key:
  [event] KEY_PRESS_PRE
  [hook] enhanced_input.key_press_pre_handler
  [event] KEY_PRESS
  [hook] input_handler.process_key
  [event] USER_INPUT_PRE
  [hook] workflow_enforcement.validate_input
  [event] USER_INPUT
  [hook] llm_service.process_user_message
  [event] LLM_REQUEST_PRE
  [hook] api_communication.prepare_request

step 4: analyze the flow
  - identify which hooks modified data
  - check for any cancelled events
  - note timing for each phase


example workflow 2:

scenario: "why is my command slow?"

step 1: measure timing
  <terminal>grep "SLASH_COMMAND" .kollab/logs/kollab.log | grep "duration_ms" | tail -10</terminal>

step 2: identify slow hook
  look for duration_ms values > 100

step 3: inspect hook implementation
  find the plugin with the slow hook:
    <terminal>grep -r "hook_name" plugins/ --include="*.py" -l</terminal>

  read the hook callback to find bottleneck.

step 4: optimize or report
  - optimize slow code if you own it
  - report performance issue if in core library


example workflow 3:

scenario: "which plugins are listening to RENDER_FRAME?"

step 1: find hooks for event type
  <terminal>grep -rn "RENDER_FRAME" plugins/ --include="*.py" | grep -E "Hook|EventType"</terminal>

step 2: list all listeners
  expected output shows:
    - plugin names
    - hook names
    - priority levels

step 3: understand execution order
  higher priority hooks execute first.
  system (1000) > security (900) > preprocessing (500) >
  llm (100) > postprocessing (50) > display (10)

step 4: verify expected behavior
  check if each listener is needed for render_frame.
  remove unused hooks to improve performance.


advanced: event tracing decorator

create a tracing decorator in kollabor/events/tracer.py:

  import functools
  import time
  import logging

  logger = logging.getLogger(__name__)

  def trace_event(event_type):
      def decorator(func):
          @functools.wraps(func)
          async def wrapper(self, *args, **kwargs):
              start = time.time()
              logger.debug(f"[TRACE] {event_type} starting")
              try:
                  result = await func(self, *args, **kwargs)
                  duration = (time.time() - start) * 1000
                  logger.debug(f"[TRACE] {event_type} complete in {duration:.2f}ms")
                  return result
              except Exception as e:
                  duration = (time.time() - start) * 1000
                  logger.error(f"[TRACE] {event_type} failed after {duration:.2f}ms: {e}")
                  raise
          return wrapper
      return decorator

apply to event emission points for detailed tracing.


advanced: custom event monitor script

create monitor_events.py:

  import asyncio
  from pathlib import Path
  import sys

  sys.path.insert(0, str(Path(__file__).parent))

  from kollabor.events.bus import EventBus
  from kollabor.events.models import EventType, Hook, HookPriority

  class EventMonitor:
      def __init__(self):
          self.event_count = {}
          self.last_events = []

      async def track_event(self, event_type, data, source):
          key = event_type.value
          self.event_count[key] = self.event_count.get(key, 0) + 1
          self.last_events.append({
              "type": key,
              "source": source,
              "timestamp": asyncio.get_event_loop().time()
          })
          if len(self.last_events) > 100:
              self.last_events.pop(0)
          print(f"[EVENT] {key} from {source}")

      def print_summary(self):
          print("\nevent count summary:")
          for event, count in sorted(self.event_count.items(), key=lambda x: -x[1]):
              print(f"  {event}: {count}")

          print("\nrecent events:")
          for event in self.last_events[-10:]:
              print(f"  {event}")

  async def main():
      bus = EventBus()
      monitor = EventMonitor()

      # register monitor hooks for all event types
      for event_type in EventType:
          hook = Hook(
              name="monitor",
              plugin_name="event_monitor",
              event_type=event_type,
              priority=HookPriority.SYSTEM.value,
              callback=lambda d, e, et=event_type: monitor.track_event(et, d, e.source)
          )
          await bus.register_hook(hook)

      print("event monitor running. press ctrl+c to stop.")
      print("trigger events in the application...\n")

      try:
          while True:
              await asyncio.sleep(1)
      except KeyboardInterrupt:
          monitor.print_summary()

  asyncio.run(main())


advanced: event flow visualization

create a text-based event flow diagram:

  1. enable event logging
  2. trigger a user action
  3. extract event sequence from logs:
     <terminal>grep "event.*from\|phase" .kollab/logs/kollab.log | grep "USER_INPUT" > event_flow.txt</terminal>
  4. format as flow diagram:

  USER_INPUT_PRE
    |-- enhanced_input.validate_format [2ms]
    +-- workflow_enforcement.check_rules [1ms]
  USER_INPUT
    |-- llm_service.prepare_request [5ms]
    +-- conversation_manager.add_to_history [1ms]
  USER_INPUT_POST
    +-- display.update_status [1ms]

  total: 10ms


troubleshooting tips:

tip 1: no events appearing in logs
  - verify logging level is set to DEBUG
  - check event_bus is properly initialized
  - verify emit_with_hooks is being called
  - add print statement at emit_with_hooks entry

tip 2: events firing but hooks not executing
  - check hook status: "hook_enabled: false"
  - verify hook.event_type matches emitted event
  - check for event cancellation by earlier hooks
  - verify hook priority (lower than expected?)

tip 3: performance degradation over time
  - monitor event_count for unexpected growth
  - check for memory leaks in event data
  - look for hooks that don't clean up resources
  - profile hook callback functions

tip 4: missing pre/post phases
  - verify event type is in pre_post_map
  - check eventprocessor.add_event_type_mapping for custom events
  - some events don't have pre/post phases (by design)

tip 5: event storms (too many events)
  - identify source of high-frequency events
  - check render_frame events (can fire 60x/second)
  - look for loops where hooks emit same event
  - consider debouncing high-frequency events


expected output:

when this skill executes successfully, you should be able to:

  [ ] list all event types and their listener counts
  [ ] see real-time event flow in logs
  [ ] identify which hooks are slow
  [ ] trace complete event flow for any action
  [ ] understand pre/main/post phase execution
  [ ] measure event processing performance
  [ ] visualize event flow patterns


status tags reference:

  [ok]   event system working normally
  [warn] high event volume or slow processing
  [error] event failures or system broken
  [todo]  optimization needed


quick reference commands:

show all event emission points:
  grep -rn "emit_with_hooks" kollabor/ --include="*.py"

show all registered hooks by plugin:
  grep -rn "Hook(" plugins/ --include="*.py" | grep -v "^.*:#"

monitor live events:
  tail -f .kollab/logs/kollab.log | grep -i "event"

find slow hooks (>50ms):
  grep "duration_ms" .kollab/logs/kollab.log | awk -F: '$2 > 50'

show hooks for specific event:
  grep -rn "USER_INPUT" plugins/ --include="*.py" | grep "Hook("

check event cancellations:
  grep -i "cancel" .kollab/logs/kollab.log
