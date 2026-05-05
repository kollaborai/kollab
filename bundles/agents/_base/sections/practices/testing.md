testing strategy & validation

testing hierarchy:
  [1] unit tests - test individual functions/classes
  [2] integration tests - test components working together
  [3] end-to-end tests - test full user workflows
  [4] manual verification - actually run and use the feature

after any code change:
  <terminal>python -m pytest tests/</terminal>

or more targeted:
  <terminal>python -m pytest tests/test_specific.py</terminal>
  <terminal>python -m pytest tests/test_file.py::test_function</terminal>
  <terminal>python -m pytest -k "keyword"</terminal>

interpreting test results:
  [ok] green (passed): changes dont break existing functionality
  [error] red (failed): you broke something, must fix before proceeding
  [warn] yellow (warnings): investigate, may indicate issues

when tests fail:
  [1] read the failure message completely
  [2] understand what test expects vs what happened
  [3] identify which change caused failure
  [4] fix the issue (either code or test)
  [5] re-run tests to confirm fix
  [6] NEVER ignore failing tests

manual testing:

after automated tests pass:
  <terminal>python main.py</terminal>
  use the feature you just built
  verify it works as expected in real usage
  check edge cases and error conditions

testing new features:

when you add new code, add tests for it:

<create>
<file>tests/test_new_feature.py</file>
<content>
"""Tests for new feature."""
import pytest
from module import new_feature

def test_new_feature_basic():
    result = new_feature(input_data)
    assert result == expected_output

def test_new_feature_edge_case():
    result = new_feature(edge_case_input)
    assert result == edge_case_output

def test_new_feature_error_handling():
    with pytest.raises(ValueError):
        new_feature(invalid_input)
</content>
</create>

performance testing:

for performance-critical code:
  <terminal>python -m pytest tests/ --durations=10</terminal>
  <terminal>python -m cProfile -o profile.stats script.py</terminal>
  <terminal>python -c "import pstats; p=pstats.Stats('profile.stats'); p.sort_stats('cumulative'); p.print_stats(20)"</terminal>

