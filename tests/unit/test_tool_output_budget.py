"""Regression tests for lossless per-tool and aggregate output budgets."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kollabor_agent.tool_executor import ToolExecutionResult
from kollabor_agent.tool_output_budget import (
    ToolOutputArtifactStore,
    pack_tool_history_and_results,
    pack_tool_history_messages,
    pack_tool_results,
    preview_text,
)


def _result(tool_id: str, output: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_id=tool_id,
        tool_type="terminal",
        success=True,
        output=output,
    )


class TestToolOutputBudget(unittest.TestCase):
    def test_none_limits_fall_back_without_type_error(self):
        with TemporaryDirectory() as directory:
            store = ToolOutputArtifactStore(Path(directory))
            result = _result("call-none", "small output")

            stats = pack_tool_results(
                [result],
                store,
                max_result_chars=None,
                preview_chars=None,
                batch_limit_chars=None,
            )

            self.assertEqual(result.output, "small output")
            self.assertEqual(stats.remaining_chars, None)
            self.assertEqual(preview_text("small output", None), "")

    def test_output_below_both_limits_stays_inline(self):
        with TemporaryDirectory() as directory:
            store = ToolOutputArtifactStore(Path(directory))
            result = _result("call-1", "small output")

            stats = pack_tool_results(
                [result],
                store,
                max_result_chars=1000,
                preview_chars=100,
                batch_limit_chars=1000,
            )

            self.assertEqual(result.output, "small output")
            self.assertNotIn("tool_output_path", result.metadata)
            self.assertEqual(stats.spilled_count, 0)
            self.assertEqual(list(Path(directory).glob("*.output")), [])

    def test_single_oversized_result_writes_exact_output(self):
        with TemporaryDirectory() as directory:
            store = ToolOutputArtifactStore(Path(directory))
            original = "line 1\n" + ("x" * 5000)
            result = _result("call-oversized", original)

            pack_tool_results(
                [result],
                store,
                max_result_chars=1000,
                preview_chars=120,
                batch_limit_chars=5000,
            )

            artifact = Path(result.metadata["tool_output_path"])
            self.assertTrue(artifact.name.endswith(".output"))
            self.assertEqual(artifact.read_text("utf-8"), original)
            self.assertIn(str(artifact), result.output)
            self.assertLess(len(result.output), len(original))
            self.assertEqual(result.metadata["tool_output_spill_reason"], "per_result")

    def test_aggregate_budget_spills_individually_valid_results(self):
        with TemporaryDirectory() as directory:
            store = ToolOutputArtifactStore(Path(directory))
            results = [_result(f"call-{i}", "x" * 1000) for i in range(5)]

            stats = pack_tool_results(
                results,
                store,
                max_result_chars=2000,
                preview_chars=100,
                batch_limit_chars=3500,
            )

            # Every raw result is below the 2,000-character per-result limit,
            # but the five-result batch is over 3,500 characters.
            spilled = [r for r in results if r.metadata.get("tool_output_path")]
            self.assertGreaterEqual(len(spilled), 1)
            self.assertEqual(stats.raw_chars, 5000)
            self.assertLessEqual(stats.model_chars, 3500)
            self.assertEqual(len(list(Path(directory).glob("*.output"))), len(spilled))
            for result in spilled:
                artifact = Path(result.metadata["tool_output_path"])
                self.assertEqual(artifact.read_text("utf-8"), "x" * 1000)
                self.assertEqual(
                    result.metadata["tool_output_spill_reason"], "aggregate"
                )

    def test_aggregate_spill_preserves_every_result_envelope(self):
        with TemporaryDirectory() as directory:
            store = ToolOutputArtifactStore(Path(directory))
            results = [_result(f"call-{i}", "y" * 1000) for i in range(5)]

            pack_tool_results(
                results,
                store,
                max_result_chars=2000,
                preview_chars=80,
                batch_limit_chars=3500,
            )

            self.assertEqual(
                [r.tool_id for r in results], [f"call-{i}" for i in range(5)]
            )
            self.assertTrue(all(r.output for r in results))
            self.assertTrue(all(r.success for r in results))

    def test_aggregate_pointer_never_exceeds_zero_or_tiny_budget(self):
        for limit in (0, 1):
            with self.subTest(limit=limit), TemporaryDirectory() as directory:
                store = ToolOutputArtifactStore(Path(directory))
                original = "z" * 100
                result = _result("call-no-room", original)

                stats = pack_tool_results(
                    [result],
                    store,
                    max_result_chars=1000,
                    preview_chars=20,
                    batch_limit_chars=limit,
                )

                self.assertLessEqual(len(result.output), limit)
                self.assertLessEqual(stats.model_chars, limit)
                artifact = Path(result.metadata["tool_output_path"])
                self.assertEqual(artifact.read_text("utf-8"), original)

    def test_history_budget_spills_oldest_tool_result_and_keeps_pairing(self):
        with TemporaryDirectory() as directory:
            store = ToolOutputArtifactStore(Path(directory))
            messages = [
                {
                    "role": "assistant",
                    "content": "",
                    "metadata": {
                        "tool_calls": [
                            {
                                "id": "call-old",
                                "function": {"name": "terminal", "arguments": "{}"},
                            }
                        ]
                    },
                },
                {
                    "role": "tool",
                    "content": "old-output-" + ("x" * 1000),
                    "metadata": {"tool_call_id": "call-old"},
                },
                {
                    "role": "assistant",
                    "content": "",
                    "metadata": {
                        "tool_calls": [
                            {
                                "id": "call-new",
                                "function": {"name": "terminal", "arguments": "{}"},
                            }
                        ]
                    },
                },
                {
                    "role": "tool",
                    "content": "new-output-" + ("y" * 1000),
                    "metadata": {"tool_call_id": "call-new"},
                },
            ]

            stats = pack_tool_history_messages(
                messages,
                store,
                max_chars=1300,
                preview_chars=80,
            )

            old = messages[1]
            self.assertEqual(old["metadata"]["tool_call_id"], "call-old")
            self.assertIn("tool_output_path", old["metadata"])
            self.assertTrue(Path(old["metadata"]["tool_output_path"]).exists())
            self.assertIn("new-output", messages[3]["content"])
            self.assertLessEqual(stats.model_chars, 1300)
            self.assertEqual(stats.spilled_count, 1)

    def test_history_pointer_never_exceeds_zero_or_tiny_budget(self):
        for limit in (0, 1):
            with self.subTest(limit=limit), TemporaryDirectory() as directory:
                store = ToolOutputArtifactStore(Path(directory))
                messages = [
                    {
                        "role": "tool",
                        "content": "history-output-" + ("x" * 100),
                        "metadata": {"tool_call_id": "call-history"},
                    }
                ]

                stats = pack_tool_history_messages(
                    messages,
                    store,
                    max_chars=limit,
                    preview_chars=20,
                )

                self.assertLessEqual(len(messages[0]["content"]), limit)
                self.assertLessEqual(stats.model_chars, limit)
                artifact = Path(messages[0]["metadata"]["tool_output_path"])
                self.assertEqual(
                    artifact.read_text("utf-8"), "history-output-" + ("x" * 100)
                )

    def test_current_results_are_packed_before_existing_history(self):
        with TemporaryDirectory() as directory:
            store = ToolOutputArtifactStore(Path(directory))
            messages = [
                {
                    "role": "tool",
                    "content": "old-history-" + ("x" * 100),
                    "metadata": {"tool_call_id": "call-old"},
                }
            ]
            current = _result("call-current", "current output")

            history_stats, result_stats = pack_tool_history_and_results(
                messages,
                [current],
                store,
                max_chars=100,
                max_result_chars=1000,
                preview_chars=20,
            )

            self.assertEqual(current.output, "current output")
            self.assertGreater(result_stats.model_chars, 0)
            self.assertGreater(history_stats.spilled_count, 0)
            self.assertTrue(messages[0]["content"])
            self.assertLessEqual(
                history_stats.model_chars + result_stats.model_chars,
                100,
            )


if __name__ == "__main__":
    unittest.main()
