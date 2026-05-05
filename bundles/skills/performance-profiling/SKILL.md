---
name: performance-profiling
description: "Performance Profiling skill - identify bottlenecks and performance issues"
---

performance-profiling mode: MEASURE AND REPORT ONLY

when this skill is active, you follow performance investigation discipline.
this is a comprehensive guide to finding performance bottlenecks.
you DO NOT implement optimizations - you report findings for the coder agent.


PHASE 0: PROFILING TOOLKIT VERIFICATION

before conducting ANY performance analysis, verify your tools are ready.


check for python profilers

  <terminal>python -c "import cProfile; print('cProfile available')"</terminal>
  <terminal>python -c "import pstats; print('pstats available')"</terminal>

if not available (should be in stdlib):
  <terminal>python -m pip install --upgrade pip --quiet</terminal>


check for advanced profilers

  <terminal>python -c "import line_profiler; print('line_profiler installed')" 2>/dev/null || echo "line_profiler not installed"</terminal>
  <terminal>python -c "import memory_profiler; print('memory_profiler installed')" 2>/dev/null || echo "memory_profiler not installed"</terminal>
  <terminal>python -c "import py-spy; print('py-spy installed')" 2>/dev/null || echo "py-spy not installed"</terminal>

if not installed:
  <terminal>pip install line_profiler memory_profiler py-spy --quiet</terminal>

verify installation:
  <terminal>kernprof --version 2>/dev/null || echo "kernprof needs install"</terminal>
  <terminal>mprof --version 2>/dev/null || echo "mprof needs install"</terminal>


check for visualization tools

  <terminal>python -c "import snakeviz; print('snakeviz installed')" 2>/dev/null || echo "snakeviz not installed"</terminal>
  <terminal>python -c "import tuna; print('tuna installed')" 2>/dev/null || echo "tuna not installed"</terminal>

if not installed:
  <terminal>pip install snakeviz tuna --quiet</terminal>


check for system monitoring tools

  <terminal>which time 2>/dev/null || echo "time not found"</terminal>
  <terminal>which ps 2>/dev/null || echo "ps not found"</terminal>
  <terminal>which top 2>/dev/null || echo "top not found"</terminal>
  <terminal>which htop 2>/dev/null || echo "htop not found"</terminal>

these help monitor resource usage during profiling.


check project structure

  <terminal>ls -la</terminal>
  <terminal>find . -name "*.py" -type f | head -20</terminal>
  <terminal>grep -r "if __name__" --include="*.py" . 2>/dev/null | head -10</terminal>

identify:
  - main entry points
  - long-running functions
  - data processing modules
  - API endpoints


verify baseline measurements

  <terminal>time python -c "print('python works')"</terminal>

if python not responding quickly, investigate system issues first.


PHASE 1: ESTABLISHING BASELINE METRICS

you cannot improve what you do not measure.


identify performance-critical paths

  <read><file>main.py</file></read>

look for:
  - main loops
  - request handlers
  - data processing pipelines
  - file I/O operations
  - network calls
  - heavy computations

document these as primary profiling targets.


measure execution time

  <terminal>time python main.py 2>&1 | tee /tmp/baseline_time.txt</terminal>

record:
  - real (wall clock) time
  - user CPU time
  - system CPU time

baseline establishes what "normal" performance looks like.


measure memory baseline

  <terminal>python -c "import psutil; print(f'Memory: {psutil.Process().memory_info().rss / 1024 / 1024:.1f} MB')"</terminal>

record baseline memory usage before profiling.


measure with python timeit

  <terminal>python -m timeit "import main; main.main()" 2>&1 | tee /tmp/timeit_results.txt</terminal>

timeit runs code multiple times for statistical accuracy.


create reproducible workload

for consistent profiling, you need consistent input.

identify test data:
  <terminal>find . -name "test*.json" -o -name "test*.csv" -o -name "fixtures*" 2>/dev/null | head -10</terminal>

use the same workload for all profiling runs.


PHASE 2: CPU PROFILING WITH CPROFILE


basic cProfile usage

  <terminal>python -m cProfile -o /tmp/profile.stats main.py 2>&1</terminal>

this creates a binary statistics file.


view cProfile output in text

  <terminal>python -m pstats /tmp/profile.stats</terminal>

in pstats interactive mode:
  - stats 10              # show top 10 functions
  - strip                 # strip directory names
  - sort cumulative       # sort by cumulative time
  - sort time             # sort by own time
  - callers functionName  # show who calls a function
  - callees functionName  # show what function calls


view sorted by different metrics

  <terminal>python -c "import pstats; p = pstats.Stats('/tmp/profile.stats'); p.sort_stats('cumulative').print_stats(20)"</terminal>

metrics to sort by:
  - cumulative: total time including subcalls
  - time: time in function excluding subcalls
  - calls: number of calls
  - filename: file name


profile with time output

  <terminal>python -m cProfile -s time main.py 2>&1 | tee /tmp/profile_sorted.txt</terminal>

  <terminal>python -m cProfile -s cumulative main.py 2>&1 | tee /tmp/profile_cumulative.txt</terminal>


profile specific function

  <read><file>path/to/module.py</file></read>

create test script:
  <terminal>python -c "import cProfile; from module import function; cProfile.run('function()', 'function_profile.stats')"</terminal>


profile with context manager

  <read><file>path/to/module.py</file></read>

look for existing profiling code:
  <terminal>grep -rn "cProfile\|pstats" --include="*.py" . 2>/dev/null</terminal>


PHASE 3: INTERPRETING CPROFILE OUTPUT


understanding the columns

ncalls:   number of calls
tottime:  total time in function (excluding subcalls)
percall:  tottime / ncalls
cumtime:  cumulative time (including subcalls)
percall:  cumtime / ncalls
filename:lineno(function): location

key insights:
  - high cumtime + low tottime = function calls slow sub-functions
  - high tottime = function itself is slow
  - high ncalls = function called many times (optimization target)


identifying bottlenecks

look for:
  [1] functions with high cumulative time
      these are the slowest paths through code

  [2] functions called many times
      optimization candidates: memoization, caching

  [3] functions with high self-time
      the computation itself is slow

  <terminal>python -c "import pstats; p = pstats.Stats('/tmp/profile.stats'); p.sort_stats('cumulative').print_stats(30)"</terminal>


identifying hot loops

  <terminal>python -c "import pstats; p = pstats.Stats('/tmp/profile.stats'); p.sort_stats('calls').print_stats(30)"</terminal>

many calls to same function = hot loop candidate.


finding unexpected calls

  <terminal>python -c "import pstats; p = pstats.Stats('/tmp/profile.stats'); p.print_callers('function_name')"</terminal>

discover who is calling expensive functions unexpectedly.


PHASE 4: LINE PROFILING WITH LINE_PROFILER


install line_profiler

  <terminal>pip install line_profiler --quiet</terminal>

verify:
  <terminal>kernprof --version</terminal>


decorate functions for line profiling

  <read><file>path/to/module.py</file></read>

add @profile decorator to functions:
  <terminal>grep -rn "@profile" --include="*.py" . 2>/dev/null | head -10</terminal>

if decorators exist, they are for line_profiler.


run line profiler

  <terminal>kernprof -l -v main.py 2>&1 | tee /tmp/line_profile.txt</terminal>

output shows time per line of code.


understanding line profiler output

  Line #      Hits         Time  Per Hit   % Time  Line Contents
  ================================================================
       1                                           @profile
       2                                           def process(data):
       3      1000          50      0.0      5.0      result = []
       4   1000000       10000      0.0     50.0      for item in data:
       5   1000000        8000      0.0     40.0          result.append(transform(item))
       6      1000         200      0.2      5.0      return result

key metrics:
  - Hits: how many times line executed
  - Time: total time spent on this line
  - Per Hit: average time per execution
  - % Time: percentage of total function time

identify slow lines by high % Time.


profile specific functions only

  <terminal>kernprof -l -v -b main.py 2>&1 | tee /tmp/line_profile_specific.txt</terminal>

or create test script:
  <terminal>python -m line_profiler module.py::function_name 2>&1</terminal>


PHASE 5: MEMORY PROFILING


memory usage with memory_profiler

  <terminal>pip install memory_profiler --quiet</terminal>

verify:
  <terminal>mprof --version</terminal>


profile memory line by line

add @profile decorator:
  <read><file>path/to/module.py</file></read>

check for existing @profile decorators:
  <terminal>grep -rn "^@profile" --include="*.py" . 2>/dev/null</terminal>


run memory profiler

  <terminal>python -m memory_profiler main.py 2>&1 | tee /tmp/memory_profile.txt</terminal>


understanding memory profiler output

  Line #    Mem usage    Increment  Occurrences   Line Contents
  =================================================================
   1     50.0 MiB     50.0 MiB           1   @profile
   2     50.0 MiB      0.0 MiB           1   def process():
   3     50.0 MiB      0.0 MiB           1       data = []
   4    150.0 MiB    100.0 MiB       10000       for i in range(10000):
   5    150.0 MiB      0.0 MiB       10000           data.append(large_object(i))
   6     50.0 MiB   -100.0 MiB           1       return data

key metrics:
  - Mem usage: total memory at that line
  - Increment: memory change on that line
  - Occurrences: how many times executed

identify memory allocations by large increments.


track memory over time

  <terminal>mprof run main.py 2>&1</terminal>
  <terminal>mprof plot --output /tmp/memory_plot.png</terminal>

this shows memory usage timeline.


find memory leaks

run mprof for extended duration:
  <terminal>mprof run --interval 0.1 --python python main.py 2>&1</terminal>
  <terminal>mprof plot</terminal>

look for:
  - continuous memory growth
  - memory not released after operations
  - baseline increase over time


compare memory before/after

  <terminal>python -m memory_tracer --output /tmp/memory_trace.json main.py 2>&1</terminal>

or use memory_profiler with timestamps.


PHASE 6: PROFILING SPECIFIC PATTERNS


profiling I/O bound operations

  <terminal>grep -rn "open(.*r\|\.read(\|\.write(" --include="*.py" . 2>/dev/null | head -20</terminal>

I/O issues:
  - many small file reads
  - no buffering
  - synchronous I/O in loops
  - unnecessary file operations

profile I/O specifically:
  <terminal>python -m cProfile -s time main.py 2>&1 | grep -E "(read|write|open)"</terminal>


profiling network operations

  <terminal>grep -rn "requests\.\|urllib\|aiohttp\|httpx" --include="*.py" . 2>/dev/null | head -20</terminal>

network issues:
  - no connection pooling
  - requests in loops
  - no timeouts configured
  - sequential vs parallel requests

identify network hotspots:
  <terminal>python -c "import cProfile; import pstats; p = pstats.Stats('profile.stats'); p.print_stats(); p.sort_stats('cumulative'); p.print_stats('request|connect')" 2>&1</terminal>


profiling database queries

  <terminal>grep -rn "\.execute\|\.query\|\.fetchall\|\.fetchone" --include="*.py" . 2>/dev/null | head -30</terminal>

database issues:
  - N+1 query patterns
  - missing indexes
  - fetching too much data
  - no query result caching
  - queries in loops

find N+1 patterns:
  <terminal>grep -rn "for.*:" --include="*.py" . 2>/dev/null | xargs -I{} grep -l "execute\|query" {} 2>/dev/null | head -10</terminal>


profiling data processing

  <terminal>grep -rn "for.*in.*:\|while.*:" --include="*.py" . 2>/dev/null | head -30</terminal>

data processing issues:
  - nested loops (O(n^2) or worse)
  - repeated calculations
  - no memoization
  - wrong data structure choice
  - list instead of set for membership

identify algorithmic complexity:
  <read><file>path/to/algorithm.py</file></read>

look for:
  - nested loops
  - repeated function calls in loops
  - growing lists without preallocation


profiling async code

  <terminal>grep -rn "async def\|await " --include="*.py" . 2>/dev/null | head -30</terminal>

async issues:
  - not using await properly
  - blocking calls in async functions
  - sequential awaits instead of gather
  - no async/await where beneficial

profile async with cProfile:
  <terminal>python -m cProfile -o async_profile.stats -m asyncio.run main() 2>&1</terminal>


PHASE 7: SYSTEM RESOURCE PROFILING


CPU profiling with py-spy

  <terminal>pip install py-spy --quiet</terminal>

spy on running process:
  <terminal>py-spy top --pid $(pgrep -f "python main.py")</terminal>

live flame graph:
  <terminal>py-spy record --pid $(pgrep -f "python main.py") --output /tmp/py_spy.svg --duration 30</terminal>


memory sampling with py-spy

  <terminal>py-spy record --pid $(pgrep -f "python main.py") --output /tmp/py_spy_memory.svg --format memory --duration 30</terminal>


system monitoring during profiling

  <terminal>while true; do ps aux | grep python | head -5; sleep 1; done</terminal>

track CPU and memory over time.


using /usr/bin/time for detailed metrics

  <terminal>/usr/bin/time -v python main.py 2>&1 | tee /tmp/detailed_time.txt</terminal>

provides:
  - maximum resident set size
  - page faults
  - context switches
  - CPU usage percentage


PHASE 8: PROFILING WEB APPLICATIONS


profile Flask applications

  <terminal>grep -rn "from flask import\|import flask" --include="*.py" . 2>/dev/null | head -5</terminal>

if Flask exists:
  <terminal>python -c "from app import app; app.run(profile=True)"</terminal>

or use Werkzeug profiler:
  <terminal>python -m werkzeug.serving --profile app:app</terminal>


profile FastAPI applications

  <terminal>grep -rn "from fastapi import\|import fastapi" --include="*.py" . 2>/dev/null | head -5</terminal>

if FastAPI exists, use middleware:
  <read><file>path/to/main.py</file></read>

look for existing profiling middleware.


profile Django applications

  <terminal>grep -rn "from django import\|import django" --include="*.py" . 2>/dev/null | head -5</terminal>

Django debug toolbar for profiling:
  <terminal>grep -rn "debug_toolbar" --include="*.py" . 2>/dev/null</terminal>


PHASE 9: VISUALIZING PROFILE DATA


using snakeviz

  <terminal>pip install snakeviz --quiet</terminal>

view profile:
  <terminal>snakeviz /tmp/profile.stats</terminal>

opens interactive visualization in browser.


using tuna

  <terminal>pip install tuna --quiet</terminal>

visualize:
  <terminal>tuna /tmp/profile.stats</terminal>

creates interactive icicle plot.


creating flame graphs

  <terminal>pip install flameprof --quiet</terminal>

  <terminal>flameprof /tmp/profile.stats > /tmp/flamegraph.svg</terminal>

view flamegraph in browser.


PHASE 10: COMPARATIVE PROFILING


compare before/after

  <terminal>python -m cProfile -o before.stats main.py</terminal>
  <terminal>python -m cProfile -o after.stats main_modified.py</terminal>

compare:
  <terminal>python -c "import pstats; p1 = pstats.Stats('before.stats'); p2 = pstats.Stats('after.stats'); print('Before:', p1.total_calls); print('After:', p2.total_calls)"</terminal>


statistically significant measurements

  <terminal>for i in {1..10}; do time python main.py; done 2>&1 | tee /tmp/timing_samples.txt</terminal>

analyze:
  - calculate mean
  - calculate standard deviation
  - identify outliers


PHASE 11: INTERPRETING RESULTS


classify performance issues

category 1: algorithmic complexity
  - O(n^2) or worse
  - nested loops over large datasets
  - repeated work without memoization

category 2: I/O bound
  - slow file operations
  - network latency
  - database queries
  - disk I/O

category 3: memory issues
  - memory leaks
  - excessive allocations
  - large data structures
  - garbage collection pauses

category 4: concurrency issues
  - underutilized CPU
  - blocking operations
  - lack of parallelization

category 5: framework/overhead
  - abstraction layers
  - unnecessary serialization
  - reflection/dynamic code


identify quick wins

common quick wins:
  - caching computed values
  - using set/list for O(1) lookup
  - batch I/O operations
  - pre-allocating lists
  - using generators instead of lists


identify complex fixes

requires more thought:
  - algorithm redesign
  - data structure changes
  - architecture modifications
  - introducing concurrency


PHASE 12: PERFORMANCE REPORT TEMPLATE


performance analysis report template

  executive summary:
    - application: [name]
    - date: [date]
    - baseline performance: [metrics]
    - primary bottleneck: [what]

  methodology:
    - profiling tools used
    - workload description
    - measurement approach
    - limitations

  findings:

    bottleneck 1: [name]
      category: [cpu|memory|i/o|network|algorithm]
      severity: [critical|high|medium|low]
      location:
        file: [path]
        function: [name]
        lines: [range]

      evidence:
        - cProfile output showing hot function
        - percentage of total time
        - comparison to baseline

      impact:
        - current performance
        - user experience impact
        - resource cost

      recommendations:
        - specific optimization approach
        - expected improvement
        - complexity estimate

    [repeat for each bottleneck...]

  recommendations summary:
    prioritized by impact/effort:
      [1] quick wins with high impact
      [2] medium effort, medium impact
      [3] complex fixes for long-term benefit

  appendix:
    - full profile output
    - charts/graphs
    - detailed metrics


PHASE 13: COMMON PERFORMANCE PATTERNS


pattern 1: nested loops over large data

  symptoms:
    - cProfile shows high cumulative time in nested function
    - O(n^2) complexity visible in code
    - performance degrades quadratically with input size

  detection:
    <terminal>grep -rn "for.*in.*:" --include="*.py" -A2 . 2>/dev/null | grep "for.*in.*:" | head -20</terminal>

  look for: for within for with large datasets.


pattern 2: repeated expensive computation

  symptoms:
    - function called many times with same inputs
    - high ncalls in cProfile
    - result could be cached

  detection:
    <terminal>python -c "import pstats; p = pstats.Stats('/tmp/profile.stats'); p.sort_stats('calls').print_stats(20)"</terminal>

  look for: same function name repeated with high ncalls.


pattern 3: list membership on large lists

  symptoms:
    - "if item in large_list" with large list
    - O(n) lookup repeated many times

  detection:
    <terminal>grep -rn "in.*\[.*\]" --include="*.py" . 2>/dev/null | head -20</terminal>

  look for: membership tests on list literals.


pattern 4: string concatenation in loops

  symptoms:
    - building string with += in loop
    - O(n^2) due to string immutability

  detection:
    <terminal>grep -rn "for.*:.*+=" --include="*.py" . 2>/dev/null | grep -E "(str|s\\b)\\s*\\+=" | head -20</terminal>

  look for: string concatenation in loop bodies.


pattern 5: unnecessary I/O

  symptoms:
    - file opened/closed repeatedly
    - same file read multiple times
    - many small read/write operations

  detection:
    <terminal>grep -rn "with open\|open(" --include="*.py" . 2>/dev/null | wc -l</terminal>
    <terminal>grep -rn "for.*:.*open\|while.*:.*open" --include="*.py" . 2>/dev/null | head -20</terminal>


pattern 6: missing index hints

  symptoms:
    - database queries slow
    - full table scans

  detection:
    <terminal>grep -rn "\.execute\|\.query" --include="*.py" . 2>/dev/null | head -20</terminal>

  examine query patterns for unindexed lookups.


pattern 7: synchronous I/O blocking

  symptoms:
    - waiting for I/O
    - no concurrency during I/O operations

  detection:
    <terminal>grep -rn "requests\.get\|urllib\.request\|urlopen" --include="*.py" . 2>/dev/null | head -20</terminal>

  look for: synchronous HTTP calls in potentially parallelizable code.


pattern 8: large object copies

  symptoms:
    - unexpected memory allocations
    - high memory usage

  detection:
    <terminal>grep -rn "\.copy(\|list(data)\|dict(data)" --include="*.py" . 2>/dev/null | head -20</terminal>

  look for: unnecessary copying of large data structures.


pattern 9: inefficient data structure

  symptoms:
    - operation slower than expected
    - wrong complexity for access pattern

  detection:
    <terminal>grep -rn "\\[.*\\].*\\[.*\\]" --include="*.py" . 2>/dev/null | head -20</terminal>

  look for: nested list access that could be dict or set.


pattern 10:过早优化

  wait, that's the opposite problem. trust profiling, not intuition.


PHASE 14: PROFILING CHECKLIST


before profiling

  [ ] have clear performance goal
  [ ] establish baseline metrics
  [ ] identify critical path
  [ ] prepare reproducible workload
  [ ] verify tools are installed


during profiling

  [ ] use consistent workload
  [ ] run multiple iterations
  [ ] profile realistic scenarios
  [ ] measure both CPU and memory
  [ ] record environment details


analysis phase

  [ ] identify top time consumers
  [ ] identify memory allocations
  [ ] classify bottlenecks by type
  [ ] prioritize by impact
  [ ] verify findings are reproducible


reporting phase

  [ ] document methodology
  [ ] provide specific file/line references
  [ ] include evidence (screenshots, logs)
  [ ] classify severity
  [ ] suggest remediation approaches


PHASE 15: PERFORMANCE PROFILING RULES


while this skill is active, these rules are MANDATORY:

  [1] NEVER optimize without profiling first
      intuition is often wrong about performance
      measure, then optimize

  [2] ALWAYS establish a baseline
      you cannot improve what you cannot measure
      record before/after metrics

  [3] profile realistic workloads
      synthetic benchmarks may mislead
      use production-like data and scenarios

  [4] focus on hot paths
      80/20 rule: 20% of code accounts for 80% of time
      optimize the critical path first

  [5] measure twice, cut once
      verify findings with multiple approaches
      cross-check with different tools

  [6] consider both time and space
      fastest solution may use too much memory
      balance trade-offs

  [7] account for measurement overhead
      profiling itself affects performance
      understand tool overhead

  [8] document findings thoroughly
      file paths, line numbers, function names
      include evidence and metrics

  [9] prioritize by impact
      what matters most to users?
      what costs the most resources?

  [10] recommend, don't implement
      this is a research skill
      provide detailed guidance for coder agent


PHASE 16: WORKFLOW GUIDE


step 1: understand the problem

  [ ] what is the performance complaint?
  [ ] what is acceptable performance?
  [ ] when is the slowness noticed?
  [ ] what are the usage patterns?


step 2: establish baseline

  [ ] measure current performance
  [ ] identify critical path
  [ ] document system specs
  [ ] record resource usage


step 3: profile

  [ ] run cProfile for CPU analysis
  [ ] run memory_profiler for memory
  [ ] identify hot functions
  [ ] identify memory allocations
  [ ] classify bottleneck types


step 4: analyze

  [ ] review hot path code
  [ ] identify root cause
  [ ] research optimization approaches
  [ ] estimate improvement potential


step 5: report

  [ ] document findings
  [ ] provide specific recommendations
  [ ] include before/after comparison potential
  [ ] prioritize by impact/effort


step 6: validate recommendations

  [ ] ensure suggestions are actionable
  [ ] verify approach is sound
  [ ] check for side effects
  [ ] estimate improvement range


PHASE 17: INTERPRETING COMMON METRICS


wall clock time (real)

  what it measures: total elapsed time
  what it indicates: user-perceived performance
  factors: CPU + I/O + waiting


user CPU time

  what it measures: CPU time in user mode
  what it indicates: application computation
  factors: algorithmic complexity, code efficiency


system CPU time

  what it measures: CPU time in kernel mode
  what it indicates: system calls, I/O
  factors: file operations, network, context switches


memory usage (RSS)

  what it measures: resident set size
  what it indicates: physical memory used
  factors: data structures, allocations


memory increment

  what it measures: change in memory
  what it indicates: allocation at specific point
  factors: large objects, caching


hit count

  what it measures: how many times executed
  what it indicates: loop iterations, function calls
  factors: algorithm, input size


PHASE 18: TOOL REFERENCE


cProfile quick reference

  basic profile:
    python -m cProfile -o output.stats script.py

  sorted output:
    python -m cProfile -s cumulative script.py

  interactive analysis:
    python -m pstats output.stats

  useful pstats commands:
    stats [n]           # show top n
    strip               # remove path
    sort <metric>       # cumulative|time|calls
    callers <name>      # who calls function
    callees <name>      # what function calls


line_profiler quick reference

  add @profile decorator to function
  run: kernprof -l -v script.py

  or use module:
    python -m line_profiler script.py


memory_profiler quick reference

  add @profile decorator
  run: python -m memory_profiler script.py

  mprof commands:
    mprof run script.py
    mprof plot
    mprof clean


py-spy quick reference

  sudo py-spy top --pid <pid>
  sudo py-spy record --pid <pid> --output output.svg
  sudo py-spy dump --pid <pid>


FINAL REMINDERS


profiling reveals truth

your code is not slow where you think it is.
only data can tell you where to focus.
trust the profiler, not your intuition.


performance is a feature

slow applications frustrate users.
efficient applications scale better.
your analysis enables better software.


measure what matters

profile realistic scenarios.
measure user-visible latency.
optimize the critical path.

find the bottlenecks before the users do.
