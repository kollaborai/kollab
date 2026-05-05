"""Response processing and formatting for LLM outputs.

Handles response parsing, formatting, and special tag processing
for LLM responses including thinking tags and final responses.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ResponseProcessor:
    """Process and format LLM responses.

    Handles parsing of special tags, response formatting,
    and content extraction from raw LLM outputs.
    """

    def __init__(self):
        """Initialize response processor."""
        # Regex patterns for tag extraction
        self.thinking_pattern = re.compile(
            r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE
        )
        self.terminal_pattern = re.compile(
            r"<terminal>(.*?)</terminal>", re.DOTALL | re.IGNORECASE
        )
        # Tool call pattern (JSON or tagged format)
        self.tool_call_pattern = re.compile(
            r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE
        )
        # Code block pattern (markdown style)
        self.code_block_pattern = re.compile(r"```(\w*)\n(.*?)\n```", re.DOTALL)
        # Final response pattern
        self.final_response_pattern = re.compile(
            r"<final_response>(.*?)</final_response>", re.DOTALL | re.IGNORECASE
        )

        logger.info("Response processor initialized")

    async def initialize(self) -> bool:
        """Initialize the response processor."""
        self.is_initialized = True
        logger.debug("Response processor async initialization complete")
        return True

    def process_response(self, raw_response: str) -> Dict[str, Any]:
        """Process raw LLM response.

        Args:
            raw_response: Raw response from LLM

        Returns:
            Processed response with extracted components
        """
        # Extract components
        thinking_content = self._extract_thinking(raw_response)
        terminal_commands = self._extract_terminal_commands(raw_response)
        tool_calls = self._extract_tool_calls(raw_response)

        # Clean main content
        clean_content = self._clean_content(raw_response)

        # Determine response type
        response_type = self._determine_response_type(
            raw_response, thinking_content, None, tool_calls  # final  # tools
        )

        processed = {
            "type": response_type,
            "content": clean_content,
            "raw": raw_response,
            "components": {
                "thinking": thinking_content,
                "terminal_commands": terminal_commands,
            },
            "metadata": {
                "has_thinking": bool(thinking_content),
                "has_terminal_commands": bool(terminal_commands),
                "word_count": len(clean_content.split()),
                "char_count": len(clean_content),
            },
        }

        logger.debug(
            f"Processed response: type={response_type}, components={len(processed['components'])}"
        )
        return processed

    def _extract_thinking(self, content: str) -> List[str]:
        """Extract thinking tags from content.

        Args:
            content: Raw content with potential thinking tags

        Returns:
            List of thinking content blocks
        """
        matches = self.thinking_pattern.findall(content)
        return [match.strip() for match in matches if match.strip()]

    def _extract_terminal_commands(self, content: str) -> List[str]:
        """Extract terminal commands from content.

        Args:
            content: Raw content with potential terminal tags

        Returns:
            List of terminal commands
        """
        # Strip <content> blocks to avoid parsing terminal examples inside file content
        text_without_content = re.sub(
            r"<content>.*?</content>", "", content, flags=re.DOTALL | re.IGNORECASE
        )
        matches = self.terminal_pattern.findall(text_without_content)
        return [match.strip() for match in matches if match.strip()]

    def _extract_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        """Extract tool calls from content.

        Args:
            content: Raw content with potential tool calls

        Returns:
            List of parsed tool calls
        """
        tool_calls = []
        matches = self.tool_call_pattern.findall(content)

        for match in matches:
            try:
                # Try to parse as JSON
                tool_data = json.loads(match)
                tool_calls.append(tool_data)
            except json.JSONDecodeError:
                # Parse as plain text command
                lines = match.strip().split("\n")
                if lines:
                    tool_calls.append({"command": lines[0], "raw": match})

        return tool_calls

    def _extract_code_blocks(self, content: str) -> List[Dict[str, str]]:
        """Extract code blocks from content.

        Args:
            content: Raw content with potential code blocks

        Returns:
            List of code blocks with language and content
        """
        code_blocks = []
        matches = self.code_block_pattern.findall(content)

        for language, code in matches:
            code_blocks.append({"language": language or "plain", "code": code.strip()})

        return code_blocks

    def _clean_content(self, content: str) -> str:
        """Remove special tags from content.

        Args:
            content: Raw content with tags

        Returns:
            Cleaned content without special tags
        """
        # Remove thinking tags
        cleaned = self.thinking_pattern.sub("", content)

        # Remove final response tags but keep content
        final_match = self.final_response_pattern.search(cleaned)
        if final_match:
            cleaned = self.final_response_pattern.sub(final_match.group(1), cleaned)

        # Remove tool call tags
        cleaned = self.tool_call_pattern.sub("", cleaned)

        # Clean up extra whitespace
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = cleaned.strip()

        return cleaned

    def _determine_response_type(
        self, raw: str, thinking: List[str], final: Optional[str], tools: List[Dict]
    ) -> str:
        """Determine the type of response.

        Args:
            raw: Raw response content
            thinking: Extracted thinking content
            final: Extracted final response
            tools: Extracted tool calls

        Returns:
            Response type identifier
        """
        if final:
            return "final"
        elif tools:
            return "tool_execution"
        elif thinking:
            return "reasoning"
        elif "?" in raw:
            return "question"
        elif len(raw) < 100:
            return "brief"
        else:
            return "standard"

    def format_for_display(self, processed_response: Dict[str, Any]) -> str:
        """Format processed response for display.

        Args:
            processed_response: Processed response data

        Returns:
            Formatted string for display
        """
        components = processed_response.get("components", {})

        # Build display string
        display_parts = []

        # Add thinking if present (dimmed)
        thinking = components.get("thinking", [])
        if thinking:
            for thought in thinking:
                display_parts.append(f"[dim]{thought}[/dim]")
                display_parts.append("")

        # Add main content or final response
        if components.get("final_response"):
            display_parts.append(components["final_response"])
        else:
            display_parts.append(processed_response.get("content", ""))

        return "\n".join(display_parts)

    def extract_metrics(self, processed_response: Dict[str, Any]) -> Dict[str, Any]:
        """Extract metrics from processed response.

        Args:
            processed_response: Processed response data

        Returns:
            Response metrics
        """
        metadata = processed_response.get("metadata", {})
        components = processed_response.get("components", {})

        metrics = {
            "response_type": processed_response.get("type", "unknown"),
            "word_count": metadata.get("word_count", 0),
            "char_count": metadata.get("char_count", 0),
            "thinking_blocks": len(components.get("thinking", [])),
            "code_blocks": len(components.get("code_blocks", [])),
            "tool_calls": len(components.get("tool_calls", [])),
            "has_final_response": metadata.get("has_final_response", False),
            "complexity": self._assess_complexity(processed_response),
        }

        return metrics

    def _assess_complexity(self, processed_response: Dict[str, Any]) -> str:
        """Assess response complexity.

        Args:
            processed_response: Processed response data

        Returns:
            Complexity level (simple, moderate, complex)
        """
        metadata = processed_response.get("metadata", {})

        # Calculate complexity score
        score = 0

        if metadata.get("word_count", 0) > 200:
            score += 2
        elif metadata.get("word_count", 0) > 100:
            score += 1

        if metadata.get("has_thinking"):
            score += 2

        if metadata.get("has_code"):
            score += 1

        if metadata.get("has_tool_calls"):
            score += 1

        # Map score to complexity level
        if score >= 4:
            return "complex"
        elif score >= 2:
            return "moderate"
        else:
            return "simple"

    def merge_streaming_chunks(self, chunks: List[str]) -> str:
        """Merge streaming response chunks.

        Args:
            chunks: List of response chunks

        Returns:
            Merged response content
        """
        # Join chunks
        merged = "".join(chunks)

        # Fix any broken tags from chunking
        merged = self._fix_broken_tags(merged)

        return merged

    def _fix_broken_tags(self, content: str) -> str:
        """Fix broken tags from streaming.

        Args:
            content: Content with potentially broken tags

        Returns:
            Content with fixed tags
        """
        # Fix broken thinking tags
        content = re.sub(r"<thi\s*nk>", "<think>", content, flags=re.IGNORECASE)
        content = re.sub(r"</thi\s*nk>", "</think>", content, flags=re.IGNORECASE)

        # Fix broken final response tags
        content = re.sub(
            r"<final_res\s*ponse>", "<final_response>", content, flags=re.IGNORECASE
        )
        content = re.sub(
            r"</final_res\s*ponse>", "</final_response>", content, flags=re.IGNORECASE
        )

        return content

    def validate_response(self, response: str) -> Tuple[bool, List[str]]:
        """Validate response format and content.

        Args:
            response: Response to validate

        Returns:
            Tuple of (is_valid, list_of_issues)
        """
        issues = []

        # Check for unclosed tags
        if "<think>" in response and "</think>" not in response:
            issues.append("Unclosed thinking tag")

        if "<final_response>" in response and "</final_response>" not in response:
            issues.append("Unclosed final response tag")

        # Check for empty response
        if not response.strip():
            issues.append("Empty response")

        # Check for truncation indicators
        if response.endswith("...") and len(response) > 1000:
            issues.append("Response appears truncated")

        # Check for malformed JSON in tool calls
        tool_matches = self.tool_call_pattern.findall(response)
        for match in tool_matches:
            if match.strip().startswith("{"):
                try:
                    json.loads(match)
                except json.JSONDecodeError:
                    issues.append("Malformed JSON in tool call")

        is_valid = len(issues) == 0
        return is_valid, issues
