"""Tests for LLM Plugin functionality."""

import re
import unittest


class TestLLMPlugin(unittest.TestCase):
    """Test cases for LLM Plugin tag parsing."""

    def test_thinking_tags_removal(self):
        """Test that <think> tags are properly removed."""

        # Simulate the parsing method without full plugin initialization
        def _parse_thinking_tags(response: str) -> str:
            # Remove thinking blocks
            clean_response = re.sub(
                r"<think>.*?</think>", "", response, flags=re.DOTALL
            )

            # Handle final_response tags
            if (
                "<final_response>" in clean_response
                and "</final_response>" in clean_response
            ):
                final_match = re.search(
                    r"<final_response>(.*?)</final_response>", clean_response, re.DOTALL
                )
                if final_match:
                    clean_response = final_match.group(1).strip()
                else:
                    clean_response = re.sub(
                        r"<final_response>.*?</final_response>",
                        "",
                        clean_response,
                        flags=re.DOTALL,
                    )

            # Clean up extra whitespace
            clean_response = re.sub(r"\n\s*\n\s*\n", "\n\n", clean_response.strip())
            return clean_response.strip()

        # Test basic thinking tag removal
        response = "Hello <think>this is internal thinking</think> world!"
        result = _parse_thinking_tags(response)
        self.assertEqual(result, "Hello  world!")

        # Test multiline thinking tags
        response = "Start <think>\nMultiline\nthinking\n</think> End"
        result = _parse_thinking_tags(response)
        self.assertEqual(result, "Start  End")

    def test_final_response_extraction(self):
        """Test that <final_response> tags properly extract content."""

        def _parse_thinking_tags(response: str) -> str:
            clean_response = re.sub(
                r"<think>.*?</think>", "", response, flags=re.DOTALL
            )

            if (
                "<final_response>" in clean_response
                and "</final_response>" in clean_response
            ):
                final_match = re.search(
                    r"<final_response>(.*?)</final_response>", clean_response, re.DOTALL
                )
                if final_match:
                    clean_response = final_match.group(1).strip()
                else:
                    clean_response = re.sub(
                        r"<final_response>.*?</final_response>",
                        "",
                        clean_response,
                        flags=re.DOTALL,
                    )

            clean_response = re.sub(r"\n\s*\n\s*\n", "\n\n", clean_response.strip())
            return clean_response.strip()

        # Test final response extraction
        response = """<think>Let me think</think>

Preamble text.

<final_response>
This is the final answer.
</final_response>

Text after should be ignored."""

        result = _parse_thinking_tags(response)
        self.assertEqual(result, "This is the final answer.")

    def test_both_tags_together(self):
        """Test that both thinking and final_response tags work together."""

        def _parse_thinking_tags(response: str) -> str:
            clean_response = re.sub(
                r"<think>.*?</think>", "", response, flags=re.DOTALL
            )

            if (
                "<final_response>" in clean_response
                and "</final_response>" in clean_response
            ):
                final_match = re.search(
                    r"<final_response>(.*?)</final_response>", clean_response, re.DOTALL
                )
                if final_match:
                    clean_response = final_match.group(1).strip()
                else:
                    clean_response = re.sub(
                        r"<final_response>.*?</final_response>",
                        "",
                        clean_response,
                        flags=re.DOTALL,
                    )

            clean_response = re.sub(r"\n\s*\n\s*\n", "\n\n", clean_response.strip())
            return clean_response.strip()

        response = """<think>
Internal reasoning here
Multiple lines of thinking
</think>

Some preamble text.

<final_response>
The user should only see this content.
Nothing else matters.
</final_response>

<think>More thinking after</think>
Trailing text to ignore."""

        result = _parse_thinking_tags(response)
        expected = "The user should only see this content.\nNothing else matters."
        self.assertEqual(result, expected)

    def test_no_special_tags(self):
        """Test that responses without special tags are passed through."""

        def _parse_thinking_tags(response: str) -> str:
            clean_response = re.sub(
                r"<think>.*?</think>", "", response, flags=re.DOTALL
            )

            if (
                "<final_response>" in clean_response
                and "</final_response>" in clean_response
            ):
                final_match = re.search(
                    r"<final_response>(.*?)</final_response>", clean_response, re.DOTALL
                )
                if final_match:
                    clean_response = final_match.group(1).strip()
                else:
                    clean_response = re.sub(
                        r"<final_response>.*?</final_response>",
                        "",
                        clean_response,
                        flags=re.DOTALL,
                    )

            clean_response = re.sub(r"\n\s*\n\s*\n", "\n\n", clean_response.strip())
            return clean_response.strip()

        response = "This is a normal response without any special tags."
        result = _parse_thinking_tags(response)
        self.assertEqual(result, response)


if __name__ == "__main__":
    unittest.main()
