---
name: profile-performance
description: "Profile and identify performance bottlenecks in the application"
---

skill name: profile-performance

purpose:
  identify performance bottlenecks across the application.
  measures render loop performance, llm api call latency,
  plugin execution time, and memory usage to pinpoint
  slow components and optimize the user experience.


when to use:
  [ ] application feels slow or sluggish
  [ ] render loop causing high cpu usage
  [ ] llm responses taking longer than expected
  [ ] plugins causing delays
  [ ] memory usage growing over time
  [ ] terminal flickering or stuttering
  [ ] want to optimize specific components


methodology:

phase 1: render loop profiling
  measure render fps, check render cache efficiency,
  identify expensive render operations

phase 2: llm api latency analysis
  measure api call timing, check connection pool health,
  identify network-related bottlenecks

phase 3: plugin performance audit
  measure hook execution time, identify slow plugins,
  check for blocking operations

phase 4: memory and resource profiling
  check memory usage patterns, analyze conversation
  history size, identify memory leaks


tools and commands:

core files to read:
  <read><file>kollabor/application.py</file></read>
  <read><file>kollabor/io/terminal_renderer.py</file></read>
  <read><file>kollabor/llm/api_communication_service.py</file></read>
  <read><file>kollabor/events/executor.py</file></read>

configuration files:
  <read><file>.kollab/config.json</file></read>

log files for performance data:
  <read><file>.kollab/logs/kollab.log</file></read>

check render fps configuration:
  <terminal>grep -i "render_fps\|render_cache" .kollab/config.json</terminal>

check render performance logs:
  <terminal>grep -i "render" .kollab/logs/kollab.log | tail -50</terminal>

check api timing logs:
  <terminal>grep -i "api call completed\|request_duration" .kollab/logs/kollab.log | tail -30</terminal>

check plugin hook performance:
  <terminal>grep -i "hook.*performance\|hook.*ms\|hook.*slow" .kollab/logs/kollab.log</terminal>

check connection statistics:
  <terminal>grep -i "connection\|session" .kollab/logs/kollab.log | tail -30</terminal>

check for slow operations:
  <terminal>grep -i "slow\|timeout\|blocking" .kollab/logs/kollab.log</terminal>

monitor memory usage:
  <terminal>ps aux | grep -i "python.*kollabor\|python.*main.py"</terminal>

check conversation history size:
  <terminal>ls -lh .kollab/conversations/</terminal>
  <terminal>wc -l .kollab/conversations/*.jsonl 2>/dev/null | tail -1</terminal>


render loop architecture:

terminalrenderer (kollabor/io/terminal_renderer.py)
  manages terminal rendering with caching for optimization

key performance features:
  - render cache: skips rendering when content unchanged
  - buffered writes: reduces flickering by batching output
  - resize debouncing: prevents rapid re-renders during terminal resize
  - config-driven fps: terminal.render_fps (default: 20 fps)

key methods for profiling:
  - render_active_area(): main render method called in loop
  - _render_lines(): handles clearing and redrawing
  - get_render_cache_status(): inspect cache effectiveness
  - invalidate_render_cache(): force re-render

performance configuration:
  - terminal.render_cache_enabled: enable/disable caching
  - terminal.render_fps: frames per second (lower = less cpu)
  - terminal.render_error_delay: delay after render error

render loop (kollabor/application.py:567-582)
  main rendering loop that drives the ui

loop structure:
  while self.running:
      await self.renderer.render_active_area()
      await asyncio.sleep(1.0 / render_fps)

key metrics:
  - render fps: how many times per second ui refreshes
  - sleep time: 1.0 / fps (e.g., 20 fps = 0.05s sleep)
  - cache hit rate: percentage of renders skipped due to cache


api communication service (kollabor/llm/api_communication_service.py)
  handles http communication with llm endpoints

connection pool configuration:
  - http_connector_limit: total connections (default: 100)
  - http_limit_per_host: per-host limit (default: 20)
  - keepalive_timeout: connection reuse timeout (default: 30s)
  - timeout: request timeout (from profile, default: 120s)

key methods for profiling:
  - get_connection_stats(): comprehensive connection statistics
  - health_check(): perform health check on api service
  - get_api_stats(): get api communication statistics

tracked statistics:
  - total_requests: number of api calls made
  - failed_requests: number of failed requests
  - recreated_sessions: number of times session was recreated
  - connection_errors: number of connection errors
  - failure_rate_percent: calculated failure rate
  - connection_error_rate_percent: calculated connection error rate
  - session_age_seconds: how long current session has been active

request timing:
  - request_duration logged after each api call
  - includes network latency + processing time


hook executor (kollabor/events/executor.py)
  executes plugin hooks with timeout and error handling

performance features:
  - hook timeout: default 30 seconds per hook
  - error handling: continues on error unless error_action="stop"
  - execution stats: tracks hook execution results

check plugin hook monitoring for performance tracking:
  <terminal>grep -i "log_performance\|performance_threshold" .kollab/config.json</terminal>


phase 1: render loop profiling

step 1: check current render fps configuration

read config:
  <terminal>cat .kollab/config.json | grep -a 5 -b 5 "render_fps"</terminal>

look for:
  "terminal": {
    "render_fps": 20,
    "render_cache_enabled": true
  }

interpretation:
  - 20 fps = 50ms per frame (standard)
  - 30 fps = 33ms per frame (smoother, more cpu)
  - 10 fps = 100ms per frame (slower, less cpu)


step 2: check render cache effectiveness

add temporary debug code to check cache:
  in kollabor/application.py, add method:

  def debug_get_render_cache_status(self):
      """get render cache status for profiling."""
      return self.renderer.get_render_cache_status()

or check via logs:
  <terminal>grep -i "cache.*invalidat\|cache.*enabled\|content unchanged" .kollab/logs/kollab.log | tail -30</terminal>

cache hit indicators:
  - if content unchanged: render was skipped (cache hit)
  - cache invalidated: force re-render
  - no "content unchanged" logs: cache disabled or always changing


step 3: measure actual render time

add timing logs to render loop temporarily:
  in kollabor/application.py, modify _render_loop():

  async def _render_loop(self) -> none:
      import time
      logger.info("render loop starting...")
      while self.running:
          try:
              start = time.time()
              await self.renderer.render_active_area()
              elapsed = time.time() - start
              if elapsed > 0.1:  # log slow renders (>100ms)
                  logger.warning(f"slow render: {elapsed*1000:.1f}ms")

              render_fps = self.config.get("terminal.render_fps", 20)
              await asyncio.sleep(1.0 / render_fps)
          except exception as e:
              logger.error(f"render loop error: {e}")

check logs for slow renders:
  <terminal>grep "slow render" .kollab/logs/kollab.log</terminal>


step 4: identify expensive render operations

status views are rendered every frame. check if any are slow:

grep for status view providers:
  <terminal>grep -r "get_status_line\|content_provider" kollabor/io/status_renderer.py</terminal>

disable status views temporarily to isolate:
  in config.json, set:
    "terminal": {
      "status_enabled": false
    }

if performance improves, a status view is the bottleneck.


phase 2: llm api latency analysis

step 1: check api call timing in logs

find recent api calls with timing:
  <terminal>grep -i "api call completed" .kollab/logs/kollab.log | tail -20</terminal>

each log shows duration: "api call completed in 2.45s"

analyze timing:
  - < 1s: excellent
  - 1-3s: normal
  - 3-10s: slow
  - > 10s: very slow (check network/endpoint)


step 2: get connection statistics

the api service tracks connection stats. to view them:

option a: check logs for connection info
  <terminal>grep -i "session.*initiali\|http session\|connection" .kollab/logs/kollab.log | tail -20</terminal>

option b: add debug endpoint in kollabor/application.py:

  def debug_get_api_stats(self):
      """get api service statistics for profiling."""
      if hasattr(self.llm_service, 'api_service'):
          return self.llm_service.api_service.get_api_stats()
      return {"error": "api service not available"}

  def debug_get_connection_stats(self):
      """get connection pool statistics."""
      if hasattr(self.llm_service, 'api_service'):
          return self.llm_service.api_service.get_connection_stats()
      return {"error": "api service not available"}

option c: run health check
  <terminal>grep -i "health_check\|session.*healthy" .kollab/logs/kollab.log</terminal>


step 3: analyze connection pool configuration

check connection settings:
  <read><file>.kollab/config.json</file></read>

look for llm connection settings:
  "core": {
    "llm": {
      "http_connector_limit": 100,
      "http_limit_per_host": 20,
      "keepalive_timeout": 30
    }
  }

bottleneck indicators:
  - high recreated_sessions: connection issues
  - high connection_errors: network problems
  - failure_rate_percent > 10%: api issues


step 4: measure network latency

to isolate network vs processing time:

check raw interaction logs:
  <terminal>ls -lt .kollab/conversations/raw_llm_interactions_*.jsonl | head -1</terminal>
  <terminal>cat $(ls -t .kollab/conversations/raw_llm_interactions_*.jsonl | head -1) | head -1 | python -m json.tool</terminal>

timestamp comparison:
  - request timestamp vs response timestamp
  - difference includes network latency + api processing


phase 3: plugin performance audit

step 1: check hook execution timing

enable hook monitoring performance logging:
  in .kollab/config.json:
    "plugins": {
      "hook_monitoring": {
        "enabled": true,
        "log_performance": true,
        "performance_threshold_ms": 50
      }
    }

restart application and trigger some events.

check performance logs:
  <terminal>grep -i "hook.*performance\|hook.*took\|hook.*ms" .kollab/logs/kollab.log</terminal>


step 2: identify slow hooks

hooks taking longer than threshold will be logged:
  "hook example_plugin.my_hook took 150ms (threshold: 50ms)"

to fix slow hooks:
  - move blocking operations to background tasks
  - add caching for expensive computations
  - reduce work done in hot path hooks (user_input, render_frame)


step 3: profile specific plugin

identify which plugin is slow:

list loaded plugins:
  <terminal>grep -i "plugin.*load\|plugin.*init" .kollab/logs/kollab.log | head -20</terminal>

check plugin initialization time:
  <terminal>grep -a 2 "initializing plugin" .kollab/logs/kollab.log</terminal>

disable plugins one by one to isolate:
  in config.json:
    "plugins": {
      "suspect_plugin": {
        "enabled": false
      }
    }


step 4: check for blocking operations

grep for synchronous operations in async contexts:
  <terminal>grep -r "time.sleep\|subprocess\." plugins/ --include="*.py"</terminal>

replace with async alternatives:
  - time.sleep -> asyncio.sleep
  - subprocess -> asyncio.create_subprocess


phase 4: memory and resource profiling

step 1: check process memory usage

get memory usage:
  <terminal>ps aux | grep -i "python.*kollabor" | awk '{print $6}'</terminal>

output is in kb. convert to mb:
  - 100,000 kb = ~100 mb
  - 500,000 kb = ~500 mb
  - > 1,000,000 kb = > 1 gb (concerning)

monitor over time:
  <terminal>watch -n 5 'ps aux | grep -i "python.*kollabor"'</terminal>


step 2: check conversation history size

large conversation history causes:
  - slower api calls (more tokens sent)
  - higher memory usage
  - slower response processing

check conversation file sizes:
  <terminal>ls -lh .kollab/conversations/</terminal>

check message counts:
  <terminal>wc -l .kollab/conversations/*.jsonl</terminal>

configure max history:
  in .kollab/config.json:
    "core": {
      "llm": {
        "max_history": 50
      }
    }


step 3: check for memory leaks

common causes:
  - growing lists/dicts never cleared
  - event listeners not removed
  - background tasks never completing
  - circular references

check background tasks:
  <terminal>grep -i "background task\|create_task" .kollab/logs/kollab.log</terminal>

check event listener count:
  add debug code to count hooks:
  <terminal>python -c "from kollabor.events import eventbus; eb = eventbus(); print(eb.get_registry_stats())"</terminal>


step 4: profile with python tools

use memory_profiler:
  <terminal>pip install memory_profiler</terminal>
  <terminal>python -m memory_profiler main.py</terminal>

use cprofile for cpu profiling:
  <terminal>python -m cprofile -o profile.stats main.py</terminal>
  <terminal>python -c "import pstats; p = pstats.stats('profile.stats'); p.sort_stats('cumulative').print_stats(20)"</terminal>


example workflow:

scenario: "terminal feels sluggish, input lag"

step 1: check render fps
  <terminal>grep "render_fps" .kollab/config.json</terminal>
  if > 30 fps, try reducing to 20:
    "terminal": {"render_fps": 20}

step 2: check for slow renders
  add timing to _render_loop (see phase 1, step 3)
  check logs for "slow render"

step 3: check render cache
  <terminal>grep "content unchanged\|cache" .kollab/logs/kollab.log | tail -30</terminal>
  if few cache hits, something is causing constant re-renders

step 4: disable status views
  set status_enabled: false temporarily
  if performance improves, a status view is the culprit

step 5: check for expensive event handlers
  disable hook monitoring plugin temporarily
  if performance improves, the monitoring is adding overhead


example workflow 2:

scenario: "llm responses take too long"

step 1: measure api call time
  <terminal>grep "api call completed" .kollab/logs/kollab.log | tail -5</terminal>

step 2: check connection health
  <terminal>grep "session.*recreat\|connection.*error" .kollab/logs/kollab.log</terminal>

step 3: check conversation history size
  <terminal>ls -lh .kollab/conversations/</terminal>
  <terminal>wc -l .kollab/conversations/session_*.jsonl | tail -1</terminal>

step 4: reduce max history if large
  "core": {"llm": {"max_history": 30}}

step 5: check network latency
  compare timestamps in raw_llm_interactions_*.jsonl
  request timestamp vs response timestamp


example workflow 3:

scenario: "high memory usage"

step 1: check process memory
  <terminal>ps aux | grep -i "python.*kollabor"</terminal>

step 2: check conversation size
  <terminal>du -sh .kollab/conversations/</terminal>

step 3: check for memory leaks
  monitor memory over time:
    <terminal>watch -n 10 'ps aux | grep -i "python.*kollabor" | awk "{print $6}"'</terminal>

step 4: reduce history size
  "core": {"llm": {"max_history": 20}}

step 5: clear old conversations
  <terminal>rm .kollab/conversations/session_*.jsonl</terminal>
  (keeps only current session)


troubleshooting tips:

tip 1: reduce render cpu usage
  - lower render_fps to 10-15
  - ensure render_cache_enabled is true
  - disable expensive status views
  - reduce visual effects (shimmer, gradients)

tip 2: reduce llm latency
  - reduce max_history (fewer tokens = faster)
  - use faster model endpoint
  - enable streaming for perceived speed
  - check keepalive_timeout for connection reuse

tip 3: reduce plugin overhead
  - disable unused plugins
  - increase hook performance_threshold_ms
  - move heavy work to background tasks
  - avoid blocking operations in hooks

tip 4: reduce memory usage
  - reduce max_history
  - clear old conversation logs
  - check for memory leaks (growing lists)
  - restart application periodically

tip 5: identify bottlenecks scientifically
  - change one thing at a time
  - measure before and after
  - use logging, not guesses
  - focus on hot paths (render loop, input handling)


expected output:

when this skill executes successfully, you should be able to:

  [ ] measure render loop fps and identify slow renders
  [ ] check render cache hit rate
  [ ] measure llm api call latency and connection health
  [ ] identify slow plugin hooks
  [ ] monitor memory usage patterns
  [ ] optimize configuration for better performance
  [ ] isolate which component is causing slowdowns


status tags reference:

  [ok]   performance is good
  [warn] performance degraded but usable
  [error] severe performance issue
  [todo]  action needed to optimize


common optimization targets:

render loop:
  - reduce fps from 30 to 20
  - disable shimmer effect
  - disable expensive status views
  - enable render cache

llm calls:
  - reduce max_history from 90 to 30
  - use faster model
  - enable streaming
  - optimize system prompt length

plugins:
  - disable unused plugins
  - increase hook timeout threshold
  - disable hook monitoring performance logging
  - move blocking operations to background
