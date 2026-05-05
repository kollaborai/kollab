file reading:

read files:
  <read><file>path/to/file.py</file></read>
  <read><file>path/to/file.py</file><lines>10-50</lines></read>

use <read> instead of:
  <terminal>cat file.py</terminal>  // WRONG
  <read><file>file.py</file></read>  // CORRECT

strategic file reading:

wasteful:
  <read><file>massive_file.py</file></read>  // reads all 3000 lines

efficient:
  <terminal>grep -n "function_name" massive_file.py</terminal>
  // output: "247:def function_name():"
  <read><file>massive_file.py</file><lines>240-270</lines></read>

automatic dedup:
  re-reading a file you already have in context returns a short
  "stale hit" marker instead of the full content. the context service
  tracks what's in your history by content hash. if the file changed
  since your last read, you get a change summary.

  to force a fresh full re-read regardless:
    <read force="true"><file>path/to/file.py</file></read>

  see the context service reference for details on dedup and eviction.
