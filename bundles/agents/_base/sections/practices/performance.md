performance optimization

before optimizing:
  [1] measure current performance
  [2] identify actual bottlenecks (dont guess)
  [3] optimize the bottleneck
  [4] measure improvement
  [5] repeat if needed

profiling:
  <terminal>python -m cProfile -o profile.stats script.py</terminal>
  <terminal>python -c "import pstats; p=pstats.Stats('profile.stats'); p.sort_stats('cumulative'); p.print_stats(20)"</terminal>

common optimizations:
  [ok] use list comprehensions instead of loops
  [ok] cache expensive computations
  [ok] use generators for large datasets
  [ok] batch database operations
  [ok] use async for I/O-bound tasks
  [ok] use multiprocessing for CPU-bound tasks

memory profiling:
  <terminal>python -m memory_profiler script.py</terminal>
