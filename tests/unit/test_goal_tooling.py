"""Regression tests for the goal_report tag parser (multi-line evidence refs).

A recorded evidence ref is ``tool_type:output`` where output can span
multiple lines (git logs, stack traces). The parser used to drop every
line that did not contain a ``::`` separator, making multi-line refs
unciteable and producing empty-error rejections.
"""

import unittest

from kollabor.goals.tooling import GOAL_REPORT_PATTERN, _extract_goal_report


def parse(tag: str) -> dict:
    return _extract_goal_report(GOAL_REPORT_PATTERN.search(tag))


class ExtractGoalReportTests(unittest.TestCase):
    def test_simple_single_line_evidence(self):
        tag = (
            '<goal_report kind="complete" version="5" reason="done">'
            "terminal:OK pytest green :: tests pass</goal_report>"
        )
        result = parse(tag)
        self.assertEqual(result["kind"], "complete")
        self.assertEqual(result["expected_record_version"], 5)
        self.assertEqual(
            result["evidence"],
            [{"ref": "terminal:OK pytest green", "claim": "tests pass"}],
        )

    def test_multiline_ref_joined(self):
        tag = (
            '<goal_report kind="complete" reason="done">'
            "terminal:8e3f97e web-ui: MCP server management in toolbar dialog\n"
            "f746b32 web-ui: profile :: recorded git-log evidence</goal_report>"
        )
        result = parse(tag)
        self.assertEqual(len(result["evidence"]), 1)
        self.assertEqual(
            result["evidence"][0]["ref"],
            "terminal:8e3f97e web-ui: MCP server management in toolbar dialog\n"
            "f746b32 web-ui: profile",
        )
        self.assertEqual(
            result["evidence"][0]["claim"], "recorded git-log evidence"
        )

    def test_two_evidence_entries(self):
        tag = (
            '<goal_report kind="progress" reason="mid">'
            "terminal:first ref :: claim one\n"
            "terminal:second ref :: claim two</goal_report>"
        )
        result = parse(tag)
        self.assertEqual(len(result["evidence"]), 2)
        self.assertEqual(result["evidence"][1]["ref"], "terminal:second ref")

    def test_multiline_claim_continues(self):
        tag = (
            '<goal_report kind="complete" reason="done">'
            "terminal:ref :: claim line one\nclaim continues here"
            "</goal_report>"
        )
        result = parse(tag)
        self.assertEqual(len(result["evidence"]), 1)
        self.assertEqual(
            result["evidence"][0]["claim"], "claim line one\nclaim continues here"
        )


if __name__ == "__main__":
    unittest.main()
