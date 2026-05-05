---
name: debugging
description: "Systematic Debugging skill - find and fix bugs efficiently using proven methodologies"
---

debugging mode: SYSTEMATIC ELIMINATION

when this skill is active, you follow methodical debugging discipline.
this is a comprehensive guide to finding and fixing bugs systematically.


PHASE 0: DEBUGGING ENVIRONMENT VERIFICATION

before attempting ANY debugging, verify your tools are ready.


check python debugger availability

  <terminal>python -c "import pdb; print('pdb available')"</terminal>

if pdb not available:
  <terminal>pip install --upgrade pdb</terminal>

verify enhanced debugger:
  <terminal>python -c "import ipdb; print('ipdb available')" 2>/dev/null || echo "ipdb not installed"</terminal>

if ipdb not installed (recommended but optional):
  <terminal>pip install ipdb</terminal>


check logging configuration

  <terminal>python -c "import logging; print('logging module ready')"</terminal>

verify log file locations:
  <terminal>ls -la logs/ 2>/dev/null || ls -la .kollab/logs/ 2>/dev/null || echo "no logs directory"</terminal>

verify log levels:
  <terminal>grep -r "logging.setLevel\|LOG_LEVEL" . --include="*.py" 2>/dev/null | head -5</terminal>


check git for bisecting

  <terminal>git --version</terminal>

verify git repo:
  <terminal>git status</terminal>

check for clean bisect state:
  <terminal>git bisect reset 2>/dev/null || echo "no bisect in progress"</terminal>


check project structure

  <terminal>ls -la</terminal>
  <terminal>find . -name "*.py" -type f | head -20</terminal>

identify entry points:
  <terminal>cat main.py 2>/dev/null | head -30</terminal>


check test coverage for regression tests

  <terminal>python -m pytest --collect-only 2>&1 | head -20</terminal>

verify tests can run:
  <terminal>python -m pytest tests/ --collect-only -q 2>&1 | tail -5</terminal>


check for existing error logs

  <terminal>tail -50 .kollab/logs/kollab.log 2>/dev/null || tail -50 logs/*.log 2>/dev/null || echo "no recent logs"</terminal>

check for crash reports:
  <terminal>find . -name "*.crash" -o -name "*.stackdump" -o -name "core.*" 2>/dev/null</terminal>


verify strace/ltrace for system call tracing (linux)

  <terminal>which strace ltrace 2>/dev/null || echo "system tracing not available"</terminal>

macos alternative:
  <terminal>which dtruss 2>/dev/null || echo "dtruss not available (requires sudo)"</terminal>


PHASE 1: THE DEBUGGING MINDSET


understand before fixing

debugging is about understanding, not changing code.
every bug teaches you something about the system.
rush to fix = introduce more bugs.

the scientific method:

  [1] observe - what is actually happening
  [2] hypothesize - what could cause this
  [3] predict - if hypothesis is true, then X should happen
  [4] test - run experiment to verify prediction
  [5] refine - update hypothesis based on results

repeat until you understand the root cause.
only then fix the code.


reproduce first

before touching any code:

  [ ] can you reproduce the bug consistently?
  [ ] what are the exact steps to reproduce?
  [ ] what is the expected behavior?
  [ ] what is the actual behavior?
  [ ] what error messages appear?
  [ ] when did this start happening?

if you cannot reproduce it:
  - gather more information from user
  - check logs for patterns
  - identify conditions that might trigger it
  - create hypothesis and test it

a bug you cannot reproduce is a bug you cannot verify is fixed.


isolate the problem

narrow down the scope:

  is it in production or development only?
  is it specific to certain data?
  is it timing-related?
  is it platform-specific?
  is it configuration-dependent?

use binary search thinking:
  - half the code, test, still broken?
  - yes: bug is in that half
  - no: bug is in other half
  - repeat


PHASE 2: PRINT DEBUGGING


when to use print debugging

print debugging is appropriate for:
  [ok] quick investigations
  [ok] understanding control flow
  [ok] verifying variable values
  [ok] one-off debugging
  [ok] situations without debugger access

not appropriate for:
  [x] complex async code
  [x] production debugging
  [x] performance issues
  [x] race conditions


effective print statements

bad prints:
  print("here")
  print("debug")
  print(x)

good prints:
  print(f"[DEBUG] process_data: input={input_data}, length={len(input_data)}")
  print(f"[DEBUG] process_data: step 1 complete, processed={count} items")
  print(f"[DEBUG] process_data: result={result}, errors={len(errors)}")

include:
  - function name
  - step or phase
  - relevant variable values
  - context about what you expect


structured logging instead of prints

use logging module:
  import logging

  logger = logging.getLogger(__name__)

  def process_data(data):
      logger.debug("process_data called with %d items", len(data))
      logger.info("starting data processing")

      for item in data:
          logger.debug("processing item: %s", item)

      logger.info("completed processing, result=%s", result)
      return result

configure logging level:
  logging.basicConfig(level=logging.DEBUG)

advantages over print:
  - can be turned on/off globally
  - includes timestamps
  - includes file/line information
  - can log to file
  - different levels for different purposes


log levels guide

DEBUG: detailed diagnostic information
  - variable values
  - function entry/exit
  - loop iterations

INFO: confirmation of expected progress
  - application startup
  - major milestones
  - successful completions

WARNING: unexpected but recoverable
  - missing optional data
  - retries happening
  - fallback behavior used

ERROR: serious failure
  - exceptions caught
  - failed operations
  - data corruption

CRITICAL: severe failure
  - application crash
  - data loss
  - cannot continue


log viewing techniques

view last N lines:
  <terminal>tail -100 logs/app.log</terminal>

follow log in real-time:
  <terminal>tail -f logs/app.log</terminal>

filter for errors:
  <terminal>grep -i error logs/app.log</terminal>

filter for specific component:
  <terminal>grep "process_data" logs/app.log</terminal>

view context around match:
  <terminal>grep -C 5 "ERROR" logs/app.log</terminal>

count occurrences:
  <terminal>grep -c "ERROR" logs/app.log</terminal>


PHASE 3: USING PDB DEBUGGER


basic pdb commands

start pdb:
  <terminal>python -m pdb script.py</terminal>

or insert in code:
  import pdb; pdb.set_trace()

essential commands:
  l(ist)     - show current code context
  n(ext)     - execute current line, move to next
  s(tep)     - step into function calls
  c(ontinue) - run until next breakpoint
  p(rint)    - print expression
  pp         - pretty print expression
  w(here)    - show current stack frame
  u(p)       - move up stack frame
  d(own)     - move down stack frame
  b(reak)    - set breakpoint
  cl(ear)    - clear breakpoint
  h(elp)     - show help
  q(uit)     - quit debugger


setting breakpoints

set by line number:
  (Pdb) break script.py:42

set by function:
  (Pdb) break my_function

set conditional breakpoint:
  (Pdb) break script.py:42, x > 100

view breakpoints:
  (Pdb) break

clear breakpoint:
  (Pdb) clear 1

clear all breakpoints:
  (Pdb) clear


breakpoint() builtin (python 3.7+)

modern replacement for pdb.set_trace():

  def complex_function(data):
      result = process(data)
      breakpoint()  # drops into debugger
      return analyze(result)

advantages:
  - cleaner syntax
  - can be disabled via PYTHONBREAKPOINT=0 env var
  - works with custom debugger


inspecting variables

print variable:
  (Pdb) p variable_name

pretty print:
  (Pdb) pp complex_dict

examine attributes:
  (Pdb) p object.__dict__

get type:
  (Pdb) p type(variable)

get length:
  (Pdb) p len(my_list)

call methods:
  (Pdb) p my_dict.keys()

examine expression:
  (Pdb) p x + y * 2


navigating the stack

show stack trace:
  (Pdb) where

move to calling frame:
  (Pdb) up

move back to callee:
  (Pdb) down

view frame at level:
  (Pdb) frame 2

examine variables in different frame:
  (Pdb) up
  (Pdb) p local_variable
  (Pdb) down


modifying state

change variable value:
  (Pdb) variable_name = new_value

execute statement:
  (Pdb) import math; x = math.sqrt(16)

call function:
  (Pdb) result = helper_function(x)

return from function early:
  (Pdb) return value


post-mortem debugging

debug after crash:
  import pdb
  import sys

  try:
      risky_operation()
  except Exception:
      pdb.post_mortem()

or from command line:
  <terminal>python -m pdb script.py</terminal>
  # let it crash
  (Pdb) where  # shows crash location

or with exception:
  <terminal>python -m pdb -c continue script.py</terminal>


PHASE 4: USING IPDB (ENHANCED DEBUGGER)


install and configure ipdb

  <terminal>pip install ipdb</terminal>

set as default:
  export PYTHONBREAKPOINT=ipdb.set_trace

use in code:
  import ipdb; ipdb.set_trace()


ipdb advantages over pdb

  - syntax highlighting
  - tab completion
  - better stack trace display
  - context aware code display
  - easier to read


ipdb-specific commands

  h(elp)          - show help with categories
  ll              - show more source code context
  st(ep)          - step into, showing code
  n(ext)          - next, skipping function calls
  c(ontinue)      - continue to next breakpoint
  r(eturn)        - continue until function returns
  j(ump) lineno   - jump to line number


ipdb configuration

create ~/.ipdbrc:
  # ipdb configuration
  alias st step
  alias n next
  alias c continue
  alias list ll 50


PHASE 5: LOG ANALYSIS


reading log files effectively

start from the end:
  <terminal>tail -500 logs/app.log | less</terminal>

search backward in less:
  - use ? to search backward
  - use n for next match
  - use N for previous match


correlating timestamps

grep specific time range:
  <terminal>grep "2024-01-15 10:2[3-4]" logs/app.log</terminal>

find time-adjacent entries:
  <terminal>grep -A 10 -B 10 "ERROR" logs/app.log | head -30</terminal>


log pattern analysis

find unique error types:
  <terminal>grep -o "ERROR: [A-Z].*" logs/app.log | sort -u</terminal>

count error frequency:
  <terminal>grep "ERROR" logs/app.log | sort | uniq -c | sort -rn</terminal>

find exceptions:
  <terminal>grep -i "exception\|traceback" logs/app.log -A 5</terminal>


structured logging with json

use json format for parsing:
  import json
  import logging

  class JSONFormatter(logging.Formatter):
      def format(self, record):
          log_data = {
              "timestamp": self.formatTime(record),
              "level": record.levelname,
              "logger": record.name,
              "message": record.getMessage(),
              "file": record.pathname,
              "line": record.lineno
          }
          return json.dumps(log_data)

query json logs:
  <terminal>grep "process_data" logs/app.log | jq '.level, .message'</terminal>


PHASE 6: GIT BISECT FOR REGRESSIONS


when to use git bisect

use when:
  - bug appeared at some point in history
  - you know a working version exists
  - you cannot identify the problematic commit

bisect uses binary search through commits.


bisect workflow

start bisect:
  <terminal>git bisect start</terminal>

mark current as bad:
  <terminal>git bisect bad</terminal>

mark known good version:
  <terminal>git bisect good <commit-hash></terminal>
  or
  <terminal>git bisect good v1.2.0</terminal>

git will checkout a middle commit.
test if bug exists:
  [ ] if bug present: git bisect bad
  [ ] if bug absent: git bisect good

repeat until git identifies the problematic commit.


automated bisect with script

create test script:
  #!/bin/bash
  # test_bug.sh
  python -m pytest tests/test_specific.py -q
  exit $?  # exit 0 if pass, 1 if fail

make executable:
  <terminal>chmod +x test_bug.sh</terminal>

run automated bisect:
  <terminal>git bisect run ./test_bug.sh</terminal>


bisect cleanup

always reset when done:
  <terminal>git bisect reset</terminal>

view bisect log:
  <terminal>git bisect log</terminal>

replay bisect session:
  <terminal>git bisect replay < logfile</terminal>


PHASE 7: MEMORY DEBUGGING


detecting memory leaks

use tracemalloc:
  import tracemalloc

  tracemalloc.start()

  # ... code ...

  snapshot = tracemalloc.take_snapshot()
  top_stats = snapshot.statistics('lineno')

  for stat in top_stats[:10]:
      print(stat)


memory profiling with memory_profiler

  <terminal>pip install memory_profiler</terminal>

decorate function:
  from memory_profiler import profile

  @profile
  def memory_intensive_function():
      # ...
      pass

run profiler:
  <terminal>python -m memory_profiler script.py</terminal>


finding reference cycles

use gc module:
  import gc

  # collect garbage
  gc.collect()

  # get unreachable objects
  unreachable = gc.collect()
  print(f"collected {unreachable} objects")

  # get all objects
  all_objects = gc.get_objects()
  print(f"total objects: {len(all_objects)}")

  # find ref cycles
  cycles = gc.collect()
  print(f"found {cycles} cycles")


debugging with objgraph

  <terminal>pip install objgraph</terminal>

find growth:
  import objgraph

  objgraph.show_growth()

show references:
  objgraph.show_backrefs(some_object)

find common types:
  objgraph.show_most_common_types()


PHASE 8: PERFORMANCE DEBUGGING


profiling with cProfile

  <terminal>python -m cProfile -o profile.stats script.py</terminal>

view results:
  <terminal>python -c "import pstats; p=pstats.Stats('profile.stats'); p.sort_stats('cumulative'); p.print_stats(20)"</terminal>


profiling specific function

  import cProfile
  import pstats

  def profile_function(func, *args, **kwargs):
      profiler = cProfile.Profile()
      profiler.enable()
      result = func(*args, **kwargs)
      profiler.disable()
      stats = pstats.Stats(profiler)
      stats.sort_stats('cumulative')
      stats.print_stats(20)
      return result


line profiling

  <terminal>pip install line_profiler</terminal>

decorate:
  from line_profiler import LineProfiler

  @profile
  def my_function():
      # ...

profile:
  <terminal>python -m kernprof -l -v script.py</terminal>


finding hot spots

common performance issues:
  - nested loops with heavy computation
  - repeated database queries in loops
  - excessive string concatenation
  - inefficient data structure choices
  - missing indexes

identify by:
  - looking for high cumulative time
  - checking call counts (unusually high?)
  - examining functions called in loops


PHASE 9: CONCURRENCY DEBUGGING


debugging race conditions

signs of race conditions:
  - intermittent failures
  - different results each run
  - crashes only under load
  - works on dev, fails in production

debugging techniques:
  - add delays to expose timing issues
  - add logging with timestamps
  - use thread sanitizers
  - increase concurrency to make it more likely


using logging for race conditions

add thread ID to logs:
  import logging
  import threading

  formatter = logging.Formatter(
      '%(asctime)s [%(threadName)s] %(message)s'
  )

this shows which thread did what.


deadlock detection

look for:
  - multiple threads acquiring locks in different orders
  - locks held during I/O operations
  - nested lock acquisition

prevention:
  - always acquire locks in consistent order
  - use timeout for lock acquisition
  - minimize lock holding time


asyncio debugging

enable asyncio debug mode:
  import asyncio

  asyncio.run(main(), debug=True)

check for:
  - missing await on coroutines
  - blocking calls in async functions
  - never-awaited coroutines

use:
  <terminal>python -X dev script.py</terminal>


PHASE 10: COMMON BUG PATTERNS


pattern: off-by-one errors

symptoms:
  - index out of range
  - missing first/last element
  - fencepost errors

examples:
  for i in range(len(items)):  # processes all items
  for i in range(len(items) - 1):  # misses last item!
  for i in range(1, len(items)):  # misses first item!

check:
  [ ] loop boundaries
  [ ] slice indices
  [ ] range() arguments


pattern: None reference errors

symptoms:
  - AttributeError: 'NoneType' object has no attribute
  - TypeError: object of type 'None' has no len()

prevention:
  def process(data):
      if data is None:
          return None
      return data.transform()

or use exceptions:
  def process(data):
      if data is None:
          raise ValueError("data cannot be None")
      return data.transform()


pattern: mutation while iterating

symptoms:
  - unexpected behavior in loops
  - skipped or duplicated items

wrong:
  for item in items:
      if condition(item):
          items.remove(item)

correct:
  items = [item for item in items if not condition(item)]

or:
  for item in items[:]:  # iterate over copy
      if condition(item):
          items.remove(item)


pattern: string concatenation in loops

symptoms:
  - slow performance
  - high memory usage

wrong:
  result = ""
  for item in large_list:
      result += str(item)  # creates new string each time

correct:
  parts = []
  for item in large_list:
      parts.append(str(item))
  result = "".join(parts)


pattern: mutable default arguments

symptoms:
  - state persists between calls
  - unexpected data accumulation

wrong:
  def append_item(item, items=[]):
      items.append(item)
      return items

correct:
  def append_item(item, items=None):
      if items is None:
          items = []
      items.append(item)
      return items


pattern: catching too broad exceptions

wrong:
  try:
      complex_operation()
  except:  # catches everything, including SystemExit
      pass

correct:
  try:
      complex_operation()
  except (ValueError, TypeError) as e:
      logger.error("specific error: %s", e)
      raise  # or handle appropriately


PHASE 11: SYSTEMATIC ELIMINATION


divide and conquer

half the problem space:
  1. identify midpoint in code flow
  2. add logging/checkpoint
  3. run and observe
  4. is bug before or after checkpoint?
  5. repeat in affected half

example:
  def process_workflow(input_data):
      logger.debug("step 1: validate")
      validate(input_data)
      logger.debug("step 2: transform")
      result = transform(input_data)
      logger.debug("step 3: save")
      save(result)
      logger.debug("step 4: notify")
      notify(result)

if bug appears between step 2 and 3:
  - focus on transform()
  - add more granular logging inside transform()


minimization

reduce to minimal reproducible case:
  1. remove unnecessary code
  2. simplify input data
  3. isolate from dependencies
  4. create standalone test case

goal: smallest program that still shows the bug.


control the variables

change one thing at a time:
  [ ] fix one potential issue
  [ ] test
  [ ] if fixed: done
  [ ] if not fixed: revert, try next issue

multiple simultaneous changes confuse cause and effect.


PHASE 12: DEBUGGING CHECKLIST


initial investigation

  [ ] can you reproduce the bug?
  [ ] what are the exact steps?
  [ ] what is the expected vs actual behavior?
  [ ] when did this start happening?
  [ ] has anything changed recently?


information gathering

  [ ] check error messages and stack traces
  [ ] check logs for related errors
  [ ] check logs for warnings leading up to error
  [ ] check system resources (memory, disk, cpu)
  [ ] check configuration files
  [ ] check environment variables


hypothesis formation

  [ ] what is the most likely cause?
  [ ] what evidence supports this?
  [ ] what evidence contradicts this?
  [ ] what test will confirm or deny?


verification

  [ ] does your hypothesis explain all symptoms?
  [ ] can you prove the hypothesis with a test?
  [ ] does fixing the suspected issue resolve it?
  [ ] does the fix break anything else?


documentation

  [ ] document root cause
  [ ] document how to reproduce
  [ ] document the fix
  [ ] add test to prevent regression


PHASE 13: REMOTE/PRODUCTION DEBUGGING


safe production debugging

rules:
  [1] never add code that might crash
  [2] never enable expensive operations
  [3] never expose sensitive data
  [4] always use logging level changes
  [5] always have rollback plan


enable debug logging temporarily

change log level:
  import logging

  logging.getLogger('my.module').setLevel(logging.DEBUG)

reload config:
  <terminal>kill -HUP <pid></terminal>

remember to revert after debugging.


using debug endpoints

add guarded debug endpoint:
  @app.route('/debug/info')
  def debug_info():
      if not current_app.debug:
          abort(404)
      return jsonify({
          "version": VERSION,
          "config": {k: v for k, v in config.items()
                     if 'secret' not in k.lower()},
          "stats": get_stats()
      })

only available in debug mode.


core dumps for crashes

enable core dumps:
  <terminal>ulimit -c unlimited</terminal>

analyze with gdb:
  <terminal>gdb python core</terminal>

common gdb commands:
  bt    - backtrace
  f 0   - select frame 0
  p var - print variable


PHASE 14: DEBUGGING RULES (MANDATORY)


while this skill is active, these rules are MANDATORY:

  [1] REPRODUCE FIRST before attempting fixes
      if you cannot reproduce it, you cannot verify the fix
      gather more information until you can reproduce

  [2] ONE CHANGE AT A TIME
      never change multiple things simultaneously
      test each change independently

  [3] UNDERSTAND ROOT CAUSE before fixing
      a fix that "just works" without understanding
      will create more problems later

  [4] ADD TEST FOR BUG before fixing
      this ensures you can verify the fix
      and prevents regression

  [5] MINIMIZE THE REPRODUCTION CASE
      smaller test cases are easier to debug
      and prove you understand the issue

  [6] DOCUMENT YOUR FINDINGS
      write down what you found
      future debuggers will thank you

  [7] USE APPROPRIATE TOOLS
      print debugging for simple cases
      debugger for complex control flow
      logging for production issues

  [8] NEVER IGNORE WARNINGS
      warnings often precede errors
      fix the warning, not just the error

  [9] CHECK ASSUMPTIONS
      what you think is true might not be
      verify with code/tests

  [10] FIX THE ROOT, NOT THE SYMPTOM
      error handling that swallows exceptions
      masks the real problem


FINAL REMINDERS


debugging is learning

every bug improves your understanding.
the system is teaching you something.
listen to what it says.


slow is smooth, smooth is fast

systematic debugging beats quick fixes.
the time you spend understanding
saves time later.


the answer is in the code

not in changing things randomly.
not in trying random solutions.
read the code, understand the flow.


when stuck

  [ ] step away, take a break
  [ ] explain the problem to someone else
  [ ] write down what you know
  [ ] question your assumptions
  [ ] try a different tool or approach


the goal

not just to fix the bug.
to understand why it happened.
to prevent similar bugs.
to improve the system.

now go find that bug.
