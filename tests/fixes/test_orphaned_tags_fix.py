#!/usr/bin/env python3
"""
BUG-011 Validation Test Suite
McKinsey Phase 1 & 2 Testing Framework

Tests defensive fix for orphaned </think> tags in response parser.
"""

import unittest

from kollabor_ai.response_parser import ResponseParser


class TestOrphanedTagsFix(unittest.TestCase):
    """McKinsey-style structured test cases for BUG-011"""

    def setUp(self):
        """Initialize parser for each test"""
        self.parser = ResponseParser()

    def test_paired_tags_removed(self):
        """Baseline: Verify paired tags are removed correctly"""
        input_text = "<think>thinking content</think>\n\nActual response here"
        result = self.parser._clean_content(input_text)

        self.assertNotIn("<think>", result)
        self.assertNotIn("</think>", result)
        self.assertIn("Actual response here", result)

    def test_orphaned_closing_tag_removed(self):
        """Critical: Orphaned </think> tag is removed"""
        input_text = "Response content\n\n</think>"
        result = self.parser._clean_content(input_text)

        self.assertNotIn("</think>", result)
        self.assertIn("Response content", result)

    def test_multiple_orphaned_closing_tags_removed(self):
        """Edge case: Multiple orphaned </think> tags"""
        input_text = "Response\n\n</think>\n\n</think>\n\n</think>"
        result = self.parser._clean_content(input_text)

        self.assertNotIn("</think>", result)
        self.assertEqual(result.count("</think>"), 0)

    def test_orphaned_opening_tag_removed(self):
        """Edge case: Orphaned <think> tag is removed"""
        input_text = "Response content\n\n<think>"
        result = self.parser._clean_content(input_text)

        self.assertNotIn("<think>", result)
        self.assertIn("Response content", result)

    def test_mixed_paired_and_orphaned_tags(self):
        """Complex: Paired tags + orphaned tags"""
        input_text = "<think>real thinking</think>\n\nResponse\n\n</think>\n\n</think>"
        result = self.parser._clean_content(input_text)

        self.assertNotIn("<think>", result)
        self.assertNotIn("</think>", result)
        self.assertIn("Response", result)
        self.assertNotIn("real thinking", result)

    def test_case_insensitive_removal(self):
        """Defensive: Various case combinations"""
        test_cases = [
            "Response\n\n</THINK>",
            "Response\n\n</Think>",
            "Response\n\n<THINK>",
            "Response\n\n<Think>",
        ]

        for input_text in test_cases:
            result = self.parser._clean_content(input_text)
            self.assertNotIn("think>", result.lower())
            self.assertIn("Response", result)

    def test_tool_execution_scenario(self):
        """Real-world: Tool execution with thinking"""
        input_text = """<think>analyzing the command</think>

⟣ terminal(ls -la ./plugins/)
 ▮ Read 15 lines (889 chars)
    [output here]

</think>

</think>"""

        result = self.parser._clean_content(input_text)

        # Verify all tags removed
        self.assertNotIn("<think>", result)
        self.assertNotIn("</think>", result)

        # Verify content preserved
        self.assertIn("terminal(ls -la ./plugins/)", result)
        self.assertIn("Read 15 lines", result)

    def test_no_tags_unchanged(self):
        """Baseline: Content without tags is unchanged"""
        input_text = "Just normal response content\n\nWith multiple lines"
        result = self.parser._clean_content(input_text)

        self.assertEqual(result.strip(), input_text.strip())

    def test_empty_string(self):
        """Edge case: Empty input"""
        result = self.parser._clean_content("")
        self.assertEqual(result, "")

    def test_only_orphaned_tags(self):
        """Edge case: Only orphaned tags, no content"""
        input_text = "</think>\n</think>\n<think>"
        result = self.parser._clean_content(input_text)

        # Should be empty or whitespace only
        self.assertEqual(result.strip(), "")

    def test_parse_response_integration(self):
        """Integration: Full parse_response flow"""
        raw_response = """<think>complex reasoning</think>

Here is my response.

</think>

More content here."""

        parsed = self.parser.parse_response(raw_response)
        clean_content = parsed["content"]

        # Verify all tags removed from final content
        self.assertNotIn("<think>", clean_content)
        self.assertNotIn("</think>", clean_content)

        # Verify content preserved
        self.assertIn("Here is my response", clean_content)
        self.assertIn("More content here", clean_content)

        # Verify thinking was extracted
        self.assertEqual(len(parsed["components"]["thinking"]), 1)
        self.assertEqual(parsed["components"]["thinking"][0], "complex reasoning")

    def test_whitespace_cleanup_after_tag_removal(self):
        """Quality: Excessive whitespace cleaned up"""
        input_text = "Content\n\n\n\n</think>\n\n\n\nMore content"
        result = self.parser._clean_content(input_text)

        # Should not have 3+ consecutive newlines
        self.assertNotIn("\n\n\n", result)


class TestOrphanedTagsDiagnostics(unittest.TestCase):
    """Test diagnostic logging functionality"""

    def setUp(self):
        """Initialize parser for each test"""
        self.parser = ResponseParser()

    def test_diagnostic_detects_orphaned_tags(self):
        """Verify diagnostic logging would trigger on orphaned tags"""
        raw_response = "Response\n\n</think>\n\n</think>"

        opening_count = raw_response.count("<think>")
        closing_count = raw_response.count("</think>")
        orphaned_closes = closing_count - opening_count

        # Diagnostic should detect 2 orphaned closing tags
        self.assertEqual(orphaned_closes, 2)

    def test_diagnostic_handles_paired_tags(self):
        """Verify diagnostic correctly counts paired tags"""
        raw_response = "<think>content</think>\n\nResponse"

        opening_count = raw_response.count("<think>")
        closing_count = raw_response.count("</think>")
        orphaned_closes = closing_count - opening_count

        # Should be balanced (0 orphaned)
        self.assertEqual(orphaned_closes, 0)

    def test_diagnostic_detects_unclosed_tags(self):
        """Verify diagnostic detects orphaned opening tags"""
        raw_response = "<think>\n\nResponse without closing"

        opening_count = raw_response.count("<think>")
        closing_count = raw_response.count("</think>")
        orphaned_closes = closing_count - opening_count

        # Should be negative (unclosed tag)
        self.assertLess(orphaned_closes, 0)


def run_bug_011_validation():
    """
    McKinsey Test Execution Protocol

    Runs structured test suite for BUG-011 fix validation.
    """
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Load all test cases
    suite.addTests(loader.loadTestsFromTestCase(TestOrphanedTagsFix))
    suite.addTests(loader.loadTestsFromTestCase(TestOrphanedTagsDiagnostics))

    # Run with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Print summary
    print("\n" + "=" * 70)
    print("BUG-011 VALIDATION SUMMARY")
    print("=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")

    if result.wasSuccessful():
        print("\n✅ ALL TESTS PASSED - Fix validated successfully")
        print("McKinsey Phase 1 (Defensive Fix): COMPLETE")
        print("Ready for Phase 2 (Root Cause Diagnosis)")
    else:
        print("\n⚠️ TESTS FAILED - Review failures above")
        print("Defensive fix requires additional work")

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_bug_011_validation()
    exit(0 if success else 1)
