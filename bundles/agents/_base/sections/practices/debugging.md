debugging techniques

systematic debugging process:
  [1] reproduce the bug reliably
  [2] identify exact error message/unexpected behavior
  [3] locate the code responsible
  [4] understand why its failing
  [5] fix root cause (not symptoms)
  [6] verify fix resolves issue
  [7] add test to prevent regression

finding the bug:
  <terminal>python script.py 2>&1 | tee error.log</terminal>
  <terminal>grep -r "error_function" .</terminal>
  <read><file>file.py</file></read>
  <terminal>grep -A10 -B10 "error_line" file.py</terminal>

common bug patterns:

import errors:
  symptom: "ModuleNotFoundError: No module named 'x'"
  cause: missing dependency, wrong import path, circular import
  fix:
    <terminal>pip list</terminal>
    <terminal>grep -r "import missing_module" .</terminal>
    <terminal>pip install missing_module</terminal>

type errors:
  symptom: "TypeError: expected str, got int"
  cause: wrong type passed to function
  fix:
    <read><file>buggy_file.py</file></read>
    <edit><file>buggy_file.py</file><find>func(123)</find><replace>func(str(123))</replace></edit>

attribute errors:
  symptom: "AttributeError: 'NoneType' object has no attribute 'x'"
  cause: variable is None when you expect an object
  fix:
    <read><file>buggy_file.py</file></read>
    <edit>
    <file>buggy_file.py</file>
    <find>obj.attribute</find>
    <replace>obj.attribute if obj else None</replace>
    </edit>

logic errors:
  symptom: wrong output, no error message
  cause: flawed logic, wrong algorithm, incorrect assumptions
  fix: trace execution step by step, add logging, verify logic

race conditions:
  symptom: intermittent failures, works sometimes
  cause: async operations, timing dependencies, shared state
  fix: proper locking, async/await, immutable data structures

debugging tools:
  <terminal>python -m pdb script.py</terminal>
  <terminal>python -m trace --trace script.py</terminal>
  <terminal>python -m dis module.py</terminal>
