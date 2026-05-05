"""Response parsing for LLM outputs with comprehensive tag support.

Handles parsing of special tags including thinking, terminal commands,
MCP tool calls, and file operations from LLM responses with clean architecture.
"""

import json
import logging
import re
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, overload

logger = logging.getLogger(__name__)


class FileOperationParser:
    """Parse file operations from LLM response without XML parser.

    Uses regex-based parsing to extract file operation blocks, treating
    tag content as raw text (no CDATA escaping needed).

    Supports 14 file operations:
    - edit: Replace content in existing file
    - create: Create new file
    - create_overwrite: Create/overwrite file
    - delete: Delete file
    - move: Move/rename file
    - copy: Copy file
    - copy_overwrite: Copy file with overwrite
    - append: Append to file
    - insert_after: Insert content after pattern
    - insert_before: Insert content before pattern
    - mkdir: Create directory
    - rmdir: Remove directory
    - read: Read file content
    - grep: Search file for pattern
    """

    def __init__(self):
        """Initialize file operation parser with compiled regex patterns."""
        # Operation-level patterns (outer tags only)
        self.edit_pattern = re.compile(r"<edit>(.*?)</edit>", re.DOTALL | re.IGNORECASE)
        self.create_pattern = re.compile(
            r"<create>(.*?)</create>", re.DOTALL | re.IGNORECASE
        )
        self.create_overwrite_pattern = re.compile(
            r"<create_overwrite>(.*?)</create_overwrite>", re.DOTALL | re.IGNORECASE
        )
        self.delete_pattern = re.compile(
            r"<delete>(.*?)</delete>", re.DOTALL | re.IGNORECASE
        )
        self.move_pattern = re.compile(r"<move>(.*?)</move>", re.DOTALL | re.IGNORECASE)
        self.copy_pattern = re.compile(r"<copy>(.*?)</copy>", re.DOTALL | re.IGNORECASE)
        self.copy_overwrite_pattern = re.compile(
            r"<copy_overwrite>(.*?)</copy_overwrite>", re.DOTALL | re.IGNORECASE
        )
        self.append_pattern = re.compile(
            r"<append>(.*?)</append>", re.DOTALL | re.IGNORECASE
        )
        self.insert_after_pattern = re.compile(
            r"<insert_after>(.*?)</insert_after>", re.DOTALL | re.IGNORECASE
        )
        self.insert_before_pattern = re.compile(
            r"<insert_before>(.*?)</insert_before>", re.DOTALL | re.IGNORECASE
        )
        self.mkdir_pattern = re.compile(
            r"<mkdir>(.*?)</mkdir>", re.DOTALL | re.IGNORECASE
        )
        self.rmdir_pattern = re.compile(
            r"<rmdir>(.*?)</rmdir>", re.DOTALL | re.IGNORECASE
        )
        self.read_pattern = re.compile(r"<read>(.*?)</read>", re.DOTALL | re.IGNORECASE)
        self.grep_pattern = re.compile(r"<grep>(.*?)</grep>", re.DOTALL | re.IGNORECASE)

        # Special pattern for agent/skill file generation
        # Uses @@@FILE/@@@END syntax to avoid XML tag conflicts
        # Format: @@@FILE path/to/file.md\ncontent\n@@@END
        # Path must contain / to be valid (prevents matching garbage like "@@@FILE blocks.")
        self.agent_files_pattern = re.compile(
            r"@@@FILE\s+(\S+/\S+)\n(.*?)@@@END", re.DOTALL
        )

        logger.debug("File operation parser initialized with 15 operation patterns")

    def parse_response(self, llm_response: str) -> List[Dict[str, Any]]:
        """Extract all file operations from LLM response.

        Args:
            llm_response: Raw LLM response text

        Returns:
            List of operation dictionaries with type and parameters
        """
        operations = []

        # FIRST: Parse @@@FILE blocks before any content stripping
        # This protects inner content from being mangled by tag stripping
        agent_files_ops, response_without_agent_files = self._parse_agent_files(
            llm_response
        )
        operations.extend(agent_files_ops)

        # Use the cleaned response for remaining parsing
        llm_response = response_without_agent_files

        # Parse operations that contain <content> blocks from full text first
        # These need the full response to extract their content properly
        operations.extend(
            self._parse_operations(
                self.edit_pattern, self._parse_edit_block, llm_response, "edit"
            )
        )
        operations.extend(
            self._parse_operations(
                self.create_pattern, self._parse_create_block, llm_response, "create"
            )
        )
        operations.extend(
            self._parse_operations(
                self.create_overwrite_pattern,
                self._parse_create_overwrite_block,
                llm_response,
                "create_overwrite",
            )
        )
        operations.extend(
            self._parse_operations(
                self.append_pattern, self._parse_append_block, llm_response, "append"
            )
        )
        operations.extend(
            self._parse_operations(
                self.insert_after_pattern,
                self._parse_insert_after_block,
                llm_response,
                "insert_after",
            )
        )
        operations.extend(
            self._parse_operations(
                self.insert_before_pattern,
                self._parse_insert_before_block,
                llm_response,
                "insert_before",
            )
        )

        # Strip <content>...</content> blocks to avoid parsing XML examples
        # inside file content as actual commands (e.g., skill files with <read> examples)
        text_without_content = re.sub(
            r"<content>.*?</content>", "", llm_response, flags=re.DOTALL | re.IGNORECASE
        )

        # Parse remaining operations from text with content blocks removed
        operations.extend(
            self._parse_operations(
                self.delete_pattern,
                self._parse_delete_block,
                text_without_content,
                "delete",
            )
        )
        operations.extend(
            self._parse_operations(
                self.move_pattern, self._parse_move_block, text_without_content, "move"
            )
        )
        operations.extend(
            self._parse_operations(
                self.copy_pattern, self._parse_copy_block, text_without_content, "copy"
            )
        )
        operations.extend(
            self._parse_operations(
                self.copy_overwrite_pattern,
                self._parse_copy_overwrite_block,
                text_without_content,
                "copy_overwrite",
            )
        )
        operations.extend(
            self._parse_operations(
                self.mkdir_pattern,
                self._parse_mkdir_block,
                text_without_content,
                "mkdir",
            )
        )
        operations.extend(
            self._parse_operations(
                self.rmdir_pattern,
                self._parse_rmdir_block,
                text_without_content,
                "rmdir",
            )
        )
        operations.extend(
            self._parse_operations(
                self.read_pattern, self._parse_read_block, text_without_content, "read"
            )
        )
        operations.extend(
            self._parse_operations(
                self.grep_pattern, self._parse_grep_block, text_without_content, "grep"
            )
        )

        if operations:
            logger.info(f"Parsed {len(operations)} file operations from response")

        return operations

    def _parse_agent_files(self, llm_response: str) -> Tuple[List[Dict[str, Any]], str]:
        """Parse @@@FILE blocks and extract file operations.

        Uses simple line-based syntax to avoid XML tag conflicts:
            @@@FILE path/to/file.md
            content with <create> <edit> examples - no conflicts
            @@@END

        This parser runs FIRST to protect inner content from tag stripping.

        Args:
            llm_response: Raw LLM response text

        Returns:
            Tuple of (operations list, response with @@@FILE blocks removed)
        """
        operations = []
        cleaned_response = llm_response

        for i, match in enumerate(self.agent_files_pattern.finditer(llm_response)):
            filepath = match.group(1).strip()
            content = match.group(2)

            # Remove trailing newline if present (before @@@END)
            if content.endswith("\n"):
                content = content[:-1]

            operations.append(
                {
                    "type": "file_create",
                    "id": f"agent_file_{i}",
                    "file": filepath,
                    "content": content,
                    "_position": match.start(),
                }
            )
            logger.debug(f"Parsed agent file operation: {filepath}")

            # Remove this block from the response
            cleaned_response = cleaned_response.replace(match.group(0), "", 1)

        if operations:
            logger.info(f"Parsed {len(operations)} file operations from @@@FILE blocks")

        return operations, cleaned_response

    def _parse_operations(
        self, pattern: re.Pattern, parser_func: Callable, text: str, op_name: str
    ) -> List[Dict[str, Any]]:
        """Generic operation parser.

        Args:
            pattern: Compiled regex pattern for operation
            parser_func: Function to parse inner content
            text: Text to search in
            op_name: Operation name for error reporting

        Returns:
            List of parsed operations
        """
        operations = []

        for i, match in enumerate(pattern.finditer(text)):
            inner_content = match.group(1)
            try:
                op = parser_func(inner_content)
                op["id"] = f"file_{op_name}_{i}"
                op["_position"] = match.start()
                operations.append(op)
                logger.debug(f"Parsed {op_name} operation: {op.get('file', 'N/A')}")
            except ValueError as e:
                logger.error(f"Invalid <{op_name}> block: {e}")
                # Build helpful error with expected format
                expected_format = self._get_expected_format(op_name)
                # Add malformed operation for error reporting
                operations.append(
                    {
                        "type": "malformed_file_op",
                        "id": f"malformed_{op_name}_{i}",
                        "operation": op_name,
                        "error": str(e),
                        "expected_format": expected_format,
                        "content_preview": (
                            inner_content[:300]
                            if len(inner_content) > 300
                            else inner_content
                        ),
                        "_position": match.start(),
                    }
                )

        return operations

    def _parse_read_operations(self, text: str) -> List[Dict[str, Any]]:
        """Parse <read> operations with force attribute support.

        Handles both forms:
            <read><file>path</file></read>
            <read force="true"><file>path</file></read>
        """
        operations = []
        for i, match in enumerate(self.read_pattern.finditer(text)):
            inner_content = match.group(1)
            raw_tag = match.group(0)
            # Detect force="true" in opening tag
            force = 'force="true"' in raw_tag[: raw_tag.index(">") + 1]
            try:
                op = self._parse_read_block(inner_content)
                if force:
                    op["force"] = True
                op["id"] = f"file_read_{i}"
                op["_position"] = match.start()
                operations.append(op)
                logger.debug(
                    f"Parsed read operation: "
                    f"{op.get('file', 'N/A')} force={force}"
                )
            except ValueError as e:
                logger.error(f"Invalid <read> block: {e}")
                expected_format = self._get_expected_format("read")
                operations.append(
                    {
                        "type": "malformed_file_op",
                        "id": f"malformed_read_{i}",
                        "operation": "read",
                        "error": str(e),
                        "expected_format": expected_format,
                        "content_preview": (
                            inner_content[:300]
                            if len(inner_content) > 300
                            else inner_content
                        ),
                        "_position": match.start(),
                    }
                )
        return operations

    def _get_expected_format(self, op_name: str) -> str:
        """Get expected format string for a file operation."""
        formats = {
            "edit": (
                "<edit>\n  <file>path/to/file</file>\n  <find>text to find</find>\n  "
                "<replace>replacement text</replace>\n</edit>"
            ),
            "create": (
                "<create>\n  <file>path/to/file</file>\n  "
                "<content>file content</content>\n</create>"
            ),
            "create_overwrite": (
                "<create_overwrite>\n  <file>path/to/file</file>\n  "
                "<content>file content</content>\n</create_overwrite>"
            ),
            "delete": "<delete>\n  <file>path/to/file</file>\n</delete>",
            "move": "<move>\n  <from>source/path</from>\n  <to>dest/path</to>\n</move>",
            "copy": "<copy>\n  <from>source/path</from>\n  <to>dest/path</to>\n</copy>",
            "append": (
                "<append>\n  <file>path/to/file</file>\n  "
                "<content>content to append</content>\n</append>"
            ),
            "read": "<read>\n  <file>path/to/file</file>\n</read>",
            "mkdir": "<mkdir>\n  <path>directory/path</path>\n</mkdir>",
            "rmdir": "<rmdir>\n  <path>directory/path</path>\n</rmdir>",
            "insert_after": (
                "<insert_after>\n  <file>path</file>\n  <pattern>match</pattern>\n  "
                "<content>new content</content>\n</insert_after>"
            ),
            "insert_before": (
                "<insert_before>\n  <file>path</file>\n  <pattern>match</pattern>\n  "
                "<content>new content</content>\n</insert_before>"
            ),
        }
        return formats.get(op_name, f"<{op_name}>...</{op_name}>")

    @overload
    def _extract_tag(
        self, tag_name: str, content: str, required: Literal[True] = True
    ) -> str: ...

    @overload
    def _extract_tag(
        self, tag_name: str, content: str, required: Literal[False]
    ) -> Optional[str]: ...

    def _extract_tag(
        self, tag_name: str, content: str, required: bool = True
    ) -> Optional[str]:
        """Extract content between tags.

        Args:
            tag_name: Tag name (without < >)
            content: Content to search in
            required: If True, raises ValueError if tag not found

        Returns:
            Content between tags, or None if not found and not required

        Raises:
            ValueError: If tag not found and required=True
        """
        pattern = re.compile(
            f"<{tag_name}>(.*?)</{tag_name}>", re.DOTALL | re.IGNORECASE
        )
        match = pattern.search(content)

        if not match:
            if required:
                raise ValueError(f"Missing required tag: <{tag_name}>")
            return None

        return match.group(1)

    def _parse_edit_block(self, content: str) -> Dict[str, Any]:
        """Parse <edit> block.

        Args:
            content: Inner content of <edit> tag

        Returns:
            Parsed operation dictionary
        """
        return {
            "type": "file_edit",
            "file": self._extract_tag("file", content).strip(),
            "find": self._extract_tag("find", content),  # Preserve whitespace
            "replace": self._extract_tag("replace", content),  # Preserve whitespace
        }

    def _parse_create_block(self, content: str) -> Dict[str, Any]:
        """Parse <create> block."""
        return {
            "type": "file_create",
            "file": self._extract_tag("file", content).strip(),
            "content": self._extract_tag("content", content),
        }

    def _parse_create_overwrite_block(self, content: str) -> Dict[str, Any]:
        """Parse <create_overwrite> block."""
        return {
            "type": "file_create_overwrite",
            "file": self._extract_tag("file", content).strip(),
            "content": self._extract_tag("content", content),
        }

    def _parse_delete_block(self, content: str) -> Dict[str, Any]:
        """Parse <delete> block."""
        return {
            "type": "file_delete",
            "file": self._extract_tag("file", content).strip(),
        }

    def _parse_move_block(self, content: str) -> Dict[str, Any]:
        """Parse <move> block."""
        return {
            "type": "file_move",
            "from": self._extract_tag("from", content).strip(),
            "to": self._extract_tag("to", content).strip(),
        }

    def _parse_copy_block(self, content: str) -> Dict[str, Any]:
        """Parse <copy> block."""
        return {
            "type": "file_copy",
            "from": self._extract_tag("from", content).strip(),
            "to": self._extract_tag("to", content).strip(),
        }

    def _parse_copy_overwrite_block(self, content: str) -> Dict[str, Any]:
        """Parse <copy_overwrite> block."""
        return {
            "type": "file_copy_overwrite",
            "from": self._extract_tag("from", content).strip(),
            "to": self._extract_tag("to", content).strip(),
        }

    def _parse_append_block(self, content: str) -> Dict[str, Any]:
        """Parse <append> block."""
        return {
            "type": "file_append",
            "file": self._extract_tag("file", content).strip(),
            "content": self._extract_tag("content", content),
        }

    def _parse_insert_after_block(self, content: str) -> Dict[str, Any]:
        """Parse <insert_after> block."""
        return {
            "type": "file_insert_after",
            "file": self._extract_tag("file", content).strip(),
            "pattern": self._extract_tag("pattern", content),
            "content": self._extract_tag("content", content),
        }

    def _parse_insert_before_block(self, content: str) -> Dict[str, Any]:
        """Parse <insert_before> block."""
        return {
            "type": "file_insert_before",
            "file": self._extract_tag("file", content).strip(),
            "pattern": self._extract_tag("pattern", content),
            "content": self._extract_tag("content", content),
        }

    def _parse_mkdir_block(self, content: str) -> Dict[str, Any]:
        """Parse <mkdir> block."""
        return {
            "type": "file_mkdir",
            "path": self._extract_tag("path", content).strip(),
        }

    def _parse_rmdir_block(self, content: str) -> Dict[str, Any]:
        """Parse <rmdir> block."""
        return {
            "type": "file_rmdir",
            "path": self._extract_tag("path", content).strip(),
        }

    def _parse_read_block(self, content: str) -> Dict[str, Any]:
        """Parse <read> block."""
        file_path = self._extract_tag("file", content).strip()
        lines_spec = self._extract_tag("lines", content, required=False)
        offset = self._extract_tag("offset", content, required=False)
        limit = self._extract_tag("limit", content, required=False)

        result: Dict[str, Any] = {"type": "file_read", "file": file_path}

        if lines_spec:
            result["lines"] = lines_spec.strip()
        if offset:
            result["offset"] = int(offset.strip())
        if limit:
            result["limit"] = int(limit.strip())

        return result

    def _parse_grep_block(self, content: str) -> Dict[str, Any]:
        """Parse <grep> block."""
        file_path = self._extract_tag("file", content).strip()
        pattern = self._extract_tag("pattern", content).strip()

        result: Dict[str, Any] = {
            "type": "file_grep",
            "file": file_path,
            "pattern": pattern,
        }

        # Optional: case_insensitive flag
        case_insensitive = self._extract_tag(
            "case_insensitive", content, required=False
        )
        if case_insensitive:
            result["case_insensitive"] = case_insensitive.strip().lower() in (
                "true",
                "1",
                "yes",
            )

        return result


class ResponseParser:
    """Parse and extract structured content from LLM responses.

    Supports multiple tag formats:
    - <think>content</think> - Thinking/reasoning content (removed from output)
    - <terminal>command</terminal> - Bash terminal commands
    - <tool name="tool_name" arg1="value" arg2="value">content</tool> - MCP tool calls
    - File operations: <edit>, <create>, <delete>, <move>, <copy>, <append>, etc.
    """

    def __init__(self):
        """Initialize response parser with compiled regex patterns."""
        # Plugin-registered tool tags (populated via register_plugin_tag)
        self._plugin_tags: List[Dict[str, Any]] = []

        # Thinking tags - removed from final output
        self.thinking_pattern = re.compile(
            r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE
        )

        # Terminal command tags with optional attributes
        # Supports: <terminal>cmd</terminal>
        #           <terminal background="true" name="server">cmd</terminal>
        #           <terminal timeout="5m" cwd="/path">cmd</terminal>
        self.terminal_pattern = re.compile(
            r"<terminal\s*([^>]*?)>(.*?)</terminal>", re.DOTALL | re.IGNORECASE
        )

        # Terminal session management tags
        # Support both body syntax: <terminal-output>name</terminal-output>
        # and attribute syntax: <terminal-output name="dev" lines="50" />
        self.terminal_status_pattern = re.compile(
            r'<terminal-status'
            r'(?:\s+name=["\']([^"\']+)["\'])?'
            r'\s*(?:/>|>(.*?)</terminal-status>)',
            re.DOTALL | re.IGNORECASE,
        )

        self.terminal_output_pattern = re.compile(
            r'<terminal-output'
            r'(?:\s+name=["\']([^"\']+)["\'])?'
            r'(?:\s+lines=["\'](\d+)["\'])?'
            r'\s*(?:/>|>(.*?)</terminal-output>)',
            re.DOTALL | re.IGNORECASE,
        )

        self.terminal_kill_pattern = re.compile(
            r'<terminal-kill'
            r'(?:\s+name=["\']([^"\']+)["\'])?'
            r'\s*(?:/>|>(.*?)</terminal-kill>)',
            re.DOTALL | re.IGNORECASE,
        )

        # MCP tool call tags with attributes
        self.tool_pattern = re.compile(
            r"<tool\s+([^>]*?)>(.*?)</tool>", re.DOTALL | re.IGNORECASE
        )

        # Native-style tool_call tags: <tool_call>name</tool_call> or with JSON args
        # Supports: <tool_call>search_nodes</tool_call>
        #           <tool_call>{"name": "search_nodes", "arguments": {...}}</tool_call>
        self.tool_call_pattern = re.compile(
            r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE
        )

        # Question gate tags - suspend tool execution when present
        self.question_pattern = re.compile(
            r"<question>(.*?)</question>", re.DOTALL | re.IGNORECASE
        )

        # File operations parser
        self.file_ops_parser = FileOperationParser()

        logger.info(
            "Response parser initialized with comprehensive tag support + file operations"
        )

    async def initialize(self) -> bool:
        """Initialize the response parser."""
        self.is_initialized = True
        logger.debug("Response parser async initialization complete")
        return True

    def register_plugin_tag(
        self,
        tag_name: str,
        pattern: "re.Pattern",
        tool_type: str,
        extract_fn: Callable,
    ) -> None:
        """Register a plugin XML tag for parsing.

        The parser will:
        - find matches using the pattern
        - call extract_fn to build tool_data from each match
        - strip matched tags from clean_content
        - include extracted tools in the parsed result

        Args:
            tag_name: human-readable name (for logging)
            pattern: compiled regex with capture groups
            tool_type: type string for tool_executor routing
            extract_fn: converts regex match -> tool_data dict
        """
        self._plugin_tags.append(
            {
                "name": tag_name,
                "pattern": pattern,
                "tool_type": tool_type,
                "extract_fn": extract_fn,
            }
        )
        logger.debug(f"Registered plugin tag: {tag_name} -> {tool_type}")

    def _extract_plugin_tools(self, content: str) -> List[Dict[str, Any]]:
        """Extract plugin-registered tools from response.

        Args:
            content: Response text to search for plugin tags

        Returns:
            List of tool data dicts extracted from registered plugin patterns
        """
        tools: List[Dict[str, Any]] = []
        for tag_def in self._plugin_tags:
            for match in tag_def["pattern"].finditer(content):
                tool_data = tag_def["extract_fn"](match)
                tool_data["type"] = tag_def["tool_type"]
                tool_data["id"] = f"{tag_def['tool_type']}_{len(tools)}"
                tool_data["raw"] = match.group(0)
                tool_data["_position"] = match.start()
                tools.append(tool_data)
        return tools

    def _mask_code_spans(self, content: str) -> tuple:
        """Replace code spans with opaque placeholders before tag scanning.

        This preserves the literal text (file paths, XML examples, etc.)
        while preventing any tags inside from being treated as real commands.
        Call _restore_code_spans() on the scan-only copy after extraction —
        the display copy (clean_content) never goes through this path.

        Returns (masked_content, restore_map) where restore_map maps
        placeholder -> original span.
        """
        restore_map = {}
        counter = [0]

        def _replace(m):
            token = f"\x00CODE{counter[0]}\x00"
            restore_map[token] = m.group(0)
            counter[0] += 1
            return token

        # <code>...</code> blocks
        masked = re.sub(
            r"<code>.*?</code>", _replace, content, flags=re.DOTALL | re.IGNORECASE
        )
        # triple-backtick fenced blocks
        masked = re.sub(r"```.*?```", _replace, masked, flags=re.DOTALL)
        # single-backtick inline spans
        masked = re.sub(r"`[^`\n]+`", _replace, masked)
        return masked, restore_map

    def _restore_code_spans(self, content: str, restore_map: dict) -> str:
        """Restore placeholders back to original code span text."""
        for token, original in restore_map.items():
            content = content.replace(token, original)
        return content

    def _restore_in_structure(self, obj: Any, restore_map: dict) -> Any:
        """Recursively restore placeholders in nested dicts/lists/strings."""
        if not restore_map:
            return obj
        if isinstance(obj, str):
            return self._restore_code_spans(obj, restore_map)
        if isinstance(obj, dict):
            return {k: self._restore_in_structure(v, restore_map) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._restore_in_structure(item, restore_map) for item in obj]
        return obj

    def parse_response(self, raw_response: str) -> Dict[str, Any]:
        """Parse LLM response and extract all components.

        Args:
            raw_response: Raw response text from LLM

        Returns:
            Parsed response with all extracted components
        """
        # Fix malformed tool calls before parsing
        raw_response = self._fix_malformed_tool_calls(raw_response)

        # Mask code spans before tag scanning so example XML inside
        # backtick/code blocks is never executed as a real command.
        # The masked copy is used only for tag extraction; clean_content
        # is derived from it but placeholders are harmless there since
        # _clean_content strips all tags and the placeholders are invisible
        # to the user (streaming already rendered the original text).
        raw_response, _code_restore = self._mask_code_spans(raw_response)

        # DIAGNOSTIC: McKinsey Phase 2 - Root cause analysis
        opening_count = raw_response.count("<think>")
        closing_count = raw_response.count("</think>")
        orphaned_closes = closing_count - opening_count

        if orphaned_closes > 0:
            logger.critical(
                f"🔍 BUG-011 DIAGNOSTIC: Found {orphaned_closes} orphaned </think> tags in RAW response"
            )
            logger.critical(
                f"Opening tags: {opening_count}, Closing tags: {closing_count}"
            )
            logger.critical(f"First 500 chars: {raw_response[:500]}")
        elif orphaned_closes < 0:
            logger.warning(
                f"🔍 BUG-011 DIAGNOSTIC: Found {abs(orphaned_closes)} orphaned <think> tags (unclosed)"
            )

        # Extract all components
        # IMPORTANT: Parse @@@FILE blocks FIRST to get cleaned response
        # This prevents XML examples inside @@@FILE from being executed
        file_operations = self.file_ops_parser.parse_response(raw_response)

        # Get response with @@@FILE blocks removed for other parsing
        _, response_without_agent_files = self.file_ops_parser._parse_agent_files(
            raw_response
        )

        # Extract other tools from the CLEANED response (not raw)
        thinking_blocks = self._extract_thinking(response_without_agent_files)
        terminal_commands = self._extract_terminal_commands(
            response_without_agent_files
        )
        tool_calls = self._extract_tool_calls(response_without_agent_files)
        question_content = self._extract_question(response_without_agent_files)

        # Clean content (remove all tags) - use cleaned response, then
        # restore any code-span placeholders so the display text is intact.
        clean_content = self._clean_content(response_without_agent_files)
        clean_content = self._restore_code_spans(clean_content, _code_restore)

        # DIAGNOSTIC: Verify defensive fix effectiveness
        if "</think>" in clean_content or "<think>" in clean_content:
            remaining_closes = clean_content.count("</think>")
            remaining_opens = clean_content.count("<think>")
            logger.error(
                f"⚠️ BUG-011 ALERT: Defensive fix FAILED - "
                f"{remaining_closes}  and {remaining_opens}  remain!"
            )
            logger.error(f"Cleaned content sample: {clean_content[:500]}")
        elif orphaned_closes > 0:
            logger.info(
                f"✅ BUG-011 SUCCESS: Defensive fix removed {orphaned_closes} orphaned tags"
            )

        # Extract plugin-registered tools
        plugin_tools = self._extract_plugin_tools(response_without_agent_files)

        # Count total tools (including plugin tools)
        total_tools = (
            len(terminal_commands)
            + len(tool_calls)
            + len(file_operations)
            + len(plugin_tools)
        )

        # Question gate: if question present, mark turn as completed but flag tools as pending
        # This causes the system to stop and wait for user input
        has_question = question_content is not None

        # Determine if turn is completed
        # Turn is completed if: no tools OR question present (tools suspended)
        # NOTE: this counts XML tools only. Native API tool_use blocks are
        # invisible here -- the queue_processor must override turn_completed
        # to False when has_native_tools is True, otherwise the model never
        # sees its tool results.
        turn_completed = (total_tools == 0) or has_question

        # Restore code-span placeholders in all extracted components so that
        # file content, tool args, etc. contain the original text rather than
        # \x00CODEn\x00 tokens.
        if _code_restore:
            file_operations = self._restore_in_structure(file_operations, _code_restore)
            tool_calls = self._restore_in_structure(tool_calls, _code_restore)
            terminal_commands = self._restore_in_structure(terminal_commands, _code_restore)
            plugin_tools = self._restore_in_structure(plugin_tools, _code_restore)
            raw_response = self._restore_code_spans(raw_response, _code_restore)

        parsed = {
            "raw": raw_response,
            "content": clean_content,
            "turn_completed": turn_completed,
            "question_gate_active": has_question and total_tools > 0,  # Tools suspended
            "components": {
                "thinking": thinking_blocks,
                "terminal_commands": terminal_commands,
                "tool_calls": tool_calls,
                "file_operations": file_operations,
                "plugin_tools": plugin_tools,
                "question": question_content,
            },
            "metadata": {
                "has_thinking": bool(thinking_blocks),
                "has_terminal_commands": bool(terminal_commands),
                "has_tool_calls": bool(tool_calls),
                "has_file_operations": bool(file_operations),
                "has_plugin_tools": bool(plugin_tools),
                "has_question": has_question,
                "total_tools": total_tools,
                "content_length": len(clean_content),
            },
        }

        logger.debug(
            f"Parsed response: {len(thinking_blocks)} thinking, "
            f"{len(terminal_commands)} terminal, {len(tool_calls)} tools, "
            f"{len(file_operations)} file ops"
        )

        return parsed

    def _extract_thinking(self, content: str) -> List[str]:
        """Extract thinking content blocks.

        Args:
            content: Raw response content

        Returns:
            List of thinking content strings
        """
        matches = self.thinking_pattern.findall(content)
        return [match.strip() for match in matches if match.strip()]

    def _extract_question(self, content: str) -> Optional[str]:
        """Extract question gate content.

        When a <question> tag is present, the agent is asking for user input
        and all tool calls should be suspended until the user responds.

        Args:
            content: Raw response content

        Returns:
            Question content if found, None otherwise
        """
        match = self.question_pattern.search(content)
        if match:
            return str(match.group(1).strip())
        return None

    def _fix_malformed_tool_calls(self, content: str) -> str:
        """Fix common malformed tool call patterns.

        Handles patterns like:
        - <terminal>read><file>path</file></read> -> <read><file>path</file></read>
        - <terminal>edit><file>path</file></edit> -> <edit><file>path</file></edit>
        - <terminal>create><file>path</file></create> -> <create><file>path</file></create>
        etc.

        Args:
            content: Raw response content

        Returns:
            Fixed content with corrected tool calls
        """
        fixed_content = content
        corrections_made = []

        # Pattern: <terminal>OPERATION><file> (where OPERATION is a file operation)
        # File operations: read, edit, create, create_overwrite, delete, move, copy,
        #                  copy_overwrite, append, insert_after, insert_before,
        #                  mkdir, rmdir, grep
        file_operations = [
            "read",
            "edit",
            "create",
            "create_overwrite",
            "delete",
            "move",
            "copy",
            "copy_overwrite",
            "append",
            "insert_after",
            "insert_before",
            "mkdir",
            "rmdir",
            "grep",
        ]

        for operation in file_operations:
            # Pattern: <terminal>operation><something>
            malformed_pattern = re.compile(rf"<terminal>{operation}>", re.IGNORECASE)

            matches = malformed_pattern.findall(fixed_content)
            if matches:
                # Replace <terminal>operation> with proper opening tag <operation>
                fixed_content = malformed_pattern.sub(f"<{operation}>", fixed_content)
                corrections_made.append(f"<terminal>{operation}> -> <{operation}>")

        if corrections_made:
            logger.info(
                f"Auto-corrected {len(corrections_made)} malformed tool call(s): {', '.join(corrections_made)}"
            )

        return fixed_content

    def _extract_terminal_commands(self, content: str) -> List[Dict[str, Any]]:
        """Extract terminal command blocks with attributes.

        Supports:
        - <terminal>cmd</terminal> - Basic foreground execution
        - <terminal background="true" name="server">cmd</terminal> - Background session
        - <terminal-status>session_name</terminal-status> - Get session status
        - <terminal-output lines="50">session_name</terminal-output> - Capture output
        - <terminal-kill>session_name</terminal-kill> - Kill session

        Args:
            content: Raw response content

        Returns:
            List of terminal command dictionaries
        """
        # Strip <content> blocks to avoid parsing terminal examples inside file content
        text_without_content = re.sub(
            r"<content>.*?</content>", "", content, flags=re.DOTALL | re.IGNORECASE
        )

        commands = []

        # Extract <terminal> tags with attributes
        for i, match in enumerate(self.terminal_pattern.finditer(text_without_content)):
            attrs_str = match.group(1).strip()
            command = match.group(2).strip()

            if command:
                commands.append(
                    {
                        "type": "terminal",
                        "id": f"terminal_{i}",
                        "command": command,
                        "background": self._parse_bool_attr(
                            attrs_str, "background", False
                        ),
                        "name": self._parse_str_attr(attrs_str, "name", None),
                        "timeout": self._parse_str_attr(attrs_str, "timeout", None),
                        "cwd": self._parse_str_attr(attrs_str, "cwd", None),
                        "raw": match.group(0),
                        "_position": match.start(),
                    }
                )

        # Extract <terminal-status> tags (body or name= attr)
        for i, match in enumerate(
            self.terminal_status_pattern.finditer(text_without_content)
        ):
            name_attr = match.group(1)  # name="..." attribute
            body = match.group(2)  # body text
            session_name = (name_attr or (body.strip() if body else "") or "").strip()
            if session_name:
                commands.append(
                    {
                        "type": "terminal_status",
                        "id": f"terminal_status_{i}",
                        "session_name": session_name,
                        "raw": match.group(0),
                        "_position": match.start(),
                    }
                )

        # Extract <terminal-output> tags (body or name=/lines= attrs)
        for i, match in enumerate(
            self.terminal_output_pattern.finditer(text_without_content)
        ):
            name_attr = match.group(1)  # name="..." attribute
            lines_str = match.group(2)  # lines="..." attribute
            body = match.group(3)  # body text
            session_name = (name_attr or (body.strip() if body else "") or "").strip()

            if session_name:
                lines = 50  # Default
                if lines_str:
                    try:
                        lines = int(lines_str)
                    except ValueError:
                        pass

                commands.append(
                    {
                        "type": "terminal_output",
                        "id": f"terminal_output_{i}",
                        "session_name": session_name,
                        "lines": lines,
                        "raw": match.group(0),
                        "_position": match.start(),
                    }
                )

        # Extract <terminal-kill> tags (body or name= attr)
        for i, match in enumerate(
            self.terminal_kill_pattern.finditer(text_without_content)
        ):
            name_attr = match.group(1)  # name="..." attribute
            body = match.group(2)  # body text
            session_name = (name_attr or (body.strip() if body else "") or "").strip()
            if session_name:
                commands.append(
                    {
                        "type": "terminal_kill",
                        "id": f"terminal_kill_{i}",
                        "session_name": session_name,
                        "raw": match.group(0),
                        "_position": match.start(),
                    }
                )

        return commands

    def _parse_bool_attr(self, attrs_str: str, name: str, default: bool) -> bool:
        """Parse boolean attribute from tag attributes string.

        Args:
            attrs_str: Attributes string (e.g., 'background="true" name="foo"')
            name: Attribute name to extract
            default: Default value if not found

        Returns:
            Boolean value
        """
        pattern = rf'{name}\s*=\s*["\']([^"\']+)["\']'
        match = re.search(pattern, attrs_str, re.IGNORECASE)
        if match:
            return match.group(1).lower() in ("true", "1", "yes")
        return default

    def _parse_str_attr(
        self, attrs_str: str, name: str, default: Optional[str] = None
    ) -> Optional[str]:
        """Parse string attribute from tag attributes string.

        Args:
            attrs_str: Attributes string (e.g., 'background="true" name="foo"')
            name: Attribute name to extract
            default: Default value if not found

        Returns:
            String value or default
        """
        pattern = rf'{name}\s*=\s*["\']([^"\']+)["\']'
        match = re.search(pattern, attrs_str, re.IGNORECASE)
        return match.group(1) if match else default

    def _extract_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        """Extract MCP tool call blocks from both <tool> and <tool_call> tags.

        Supports:
        - <tool name="tool_name" arg="value">content</tool>
        - <tool_call>tool_name</tool_call>
        - <tool_call>{"name": "tool_name", "arguments": {...}}</tool_call>

        Args:
            content: Raw response content

        Returns:
            List of tool call dictionaries
        """
        tool_calls = []
        tool_index = 0

        # Extract <tool> style calls (attribute-based)
        for match in self.tool_pattern.finditer(content):
            attributes_str, tool_content = match.groups()
            try:
                tool_info = self._parse_tool_attributes(attributes_str)
                tool_calls.append(
                    {
                        "type": "mcp_tool",
                        "id": f"mcp_tool_{tool_index}",
                        "name": tool_info.get("name", "unknown"),
                        "arguments": tool_info.get("arguments", {}),
                        "content": tool_content.strip(),
                        "raw": match.group(0),
                        "_position": match.start(),
                    }
                )
                tool_index += 1
            except Exception as e:
                logger.warning(f"Failed to parse <tool> call: {e}")
                tool_calls.append(
                    {
                        "type": "malformed_tool",
                        "id": f"malformed_{tool_index}",
                        "error": str(e),
                        "raw": match.group(0),
                        "_position": match.start(),
                    }
                )
                tool_index += 1

        # Extract <tool_call> style calls (content-based)
        for match in self.tool_call_pattern.finditer(content):
            call_content = match.group(1).strip()
            try:
                tool_call = self._parse_tool_call_content(
                    call_content, tool_index, match.start()
                )
                tool_calls.append(tool_call)
                tool_index += 1
            except Exception as e:
                logger.warning(f"Failed to parse <tool_call> content: {e}")
                tool_calls.append(
                    {
                        "type": "malformed_tool",
                        "id": f"malformed_{tool_index}",
                        "error": str(e),
                        "raw": match.group(0),
                        "_position": match.start(),
                    }
                )
                tool_index += 1

        return tool_calls

    def _parse_tool_call_content(
        self, content: str, index: int, position: int = 0
    ) -> Dict[str, Any]:
        """Parse content from <tool_call> tags.

        Supports:
        - Simple name: "search_nodes"
        - JSON format: {"name": "search_nodes", "arguments": {"query": "test"}}

        Args:
            content: Content between <tool_call> tags
            index: Tool index for ID generation

        Returns:
            Parsed tool call dictionary
        """
        content = content.strip()

        # Try JSON format first
        if content.startswith("{"):
            try:
                data = json.loads(content)
                return {
                    "type": "mcp_tool",
                    "id": f"mcp_tool_{index}",
                    "name": data.get("name", "unknown"),
                    "arguments": data.get("arguments", {}),
                    "content": "",
                    "raw": f"<tool_call>{content}</tool_call>",
                    "_position": position,
                }
            except json.JSONDecodeError:
                pass

        # Simple name format: just the tool name, maybe with inline args
        # Handle: "search_nodes" or "search_nodes query=test"
        parts = content.split(None, 1)
        tool_name = parts[0] if parts else "unknown"
        arguments = {}

        # Parse inline arguments if present
        if len(parts) > 1:
            arg_str = parts[1]
            # Try to parse key=value pairs
            for pair in re.findall(r'(\w+)=(["\']?)([^"\'=\s]+)\2', arg_str):
                key, _, value = pair
                arguments[key] = self._convert_value(value)

        return {
            "type": "mcp_tool",
            "id": f"mcp_tool_{index}",
            "name": tool_name,
            "arguments": arguments,
            "content": "",
            "raw": f"<tool_call>{content}</tool_call>",
            "_position": position,
        }

    def _parse_tool_attributes(self, attributes_str: str) -> Dict[str, Any]:
        """Parse tool tag attributes.

        Supports formats like:
        - name="file_reader" path="/etc/hosts"
        - name="search" query="python" limit="10"

        Args:
            attributes_str: Raw attributes string

        Returns:
            Parsed attributes with name and arguments
        """
        tool_info: Dict[str, Any] = {"name": None, "arguments": {}}

        # Parse attributes using regex to handle quoted values
        attr_pattern = r'(\w+)=(?:"([^"]*)"|\'([^\']*)\'|([^\s]+))'
        matches = re.findall(attr_pattern, attributes_str)

        for attr_name, quoted_val1, quoted_val2, unquoted_val in matches:
            value = quoted_val1 or quoted_val2 or unquoted_val

            if attr_name == "name":
                tool_info["name"] = value
            else:
                # Convert value to appropriate type
                tool_info["arguments"][attr_name] = self._convert_value(value)

        return tool_info

    def _convert_value(self, value: str) -> Any:
        """Convert string value to appropriate Python type.

        Args:
            value: String value to convert

        Returns:
            Converted value (str, int, float, bool, or original)
        """
        if not value:
            return value

        # Try boolean
        if value.lower() in ("true", "false"):
            return value.lower() == "true"

        # Try integer
        try:
            if "." not in value:
                return int(value)
        except ValueError:
            pass

        # Try float
        try:
            return float(value)
        except ValueError:
            pass

        # Return as string
        return value

    def _sanitize_thinking_tags(self, content: str) -> str:
        """Sanitize thinking tags with improved edge case handling.

        Handles:
        - Properly paired <thought>...</thought> tags
        - Orphaned closing tags (</thought> without opening)
        - Orphaned opening tags (<thought> without closing)
        - Nested and malformed tag structures

        Args:
            content: Raw content with thinking tags

        Returns:
            Cleaned content with all thinking tags removed
        """
        cleaned = content
        fix_stats = {
            "paired_removed": 0,
            "orphaned_closing_removed": 0,
            "orphaned_opening_removed": 0,
        }

        # Phase 1: Remove properly paired tags first (most common case)
        paired_matches = list(self.thinking_pattern.finditer(cleaned))
        if paired_matches:
            # Remove in reverse order to maintain correct positions
            for match in reversed(paired_matches):
                cleaned = cleaned[: match.start()] + cleaned[match.end() :]
                fix_stats["paired_removed"] += 1

        # Phase 2: Handle orphaned closing tags (</thought>, </think> without opening)
        # These can appear when LLM outputs closing tags from examples
        # Handle both "thought" and "think" variations with case insensitivity
        orphaned_closing_patterns = [
            re.compile(r"</thought>", re.IGNORECASE),
            re.compile(r"</think>", re.IGNORECASE),
        ]
        for pattern in orphaned_closing_patterns:
            matches = list(pattern.finditer(cleaned))
            if matches:
                for match in reversed(matches):
                    cleaned = cleaned[: match.start()] + cleaned[match.end() :]
                    fix_stats["orphaned_closing_removed"] += 1

        # Phase 3: Handle orphaned opening tags (<thought>, <think> without closing)
        # These can appear when LLM starts a thought block but doesn't finish
        orphaned_opening_patterns = [
            re.compile(r"<thought>", re.IGNORECASE),
            re.compile(r"<think>", re.IGNORECASE),
        ]
        for pattern in orphaned_opening_patterns:
            matches = list(pattern.finditer(cleaned))
            if matches:
                for match in reversed(matches):
                    cleaned = cleaned[: match.start()] + cleaned[match.end() :]
                    fix_stats["orphaned_opening_removed"] += 1

        # Log if any fixes were applied
        total_fixes = sum(fix_stats.values())
        if total_fixes > 0:
            logger.debug(f"Tag sanitization applied: {fix_stats}")

        return cleaned

    def _clean_content(self, content: str) -> str:
        """Remove all special tags from content.

        Args:
            content: Raw content with tags

        Returns:
            Cleaned content without any special tags
        """
        # Remove thinking tags using improved sanitization
        cleaned = self._sanitize_thinking_tags(content)

        # Remove terminal tags but preserve content structure
        cleaned = self.terminal_pattern.sub("", cleaned)
        cleaned = self.terminal_status_pattern.sub("", cleaned)
        cleaned = self.terminal_output_pattern.sub("", cleaned)
        cleaned = self.terminal_kill_pattern.sub("", cleaned)

        # Remove orphaned closing tags that survived paired-tag stripping
        # LLMs frequently output </terminal>, </agent>, etc. without openers
        cleaned = re.sub(
            r"</(?:terminal|agent|status|capture|stop|message|clone|team|broadcast)>\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        # Remove orphaned opening tags (e.g. <terminal ...> without </terminal>)
        cleaned = re.sub(
            r"<(?:terminal|agent|capture|stop|clone|team|broadcast)\s*[^>]*>\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

        # Remove tool tags but preserve content structure
        cleaned = self.tool_pattern.sub("", cleaned)

        # Remove <tool_call> tags
        cleaned = self.tool_call_pattern.sub("", cleaned)

        # Remove question tags but preserve content for display
        # The question content stays visible, just the tags are removed
        cleaned = self.question_pattern.sub(r"\1", cleaned)

        # Remove file operation tags (all 14 types)
        # Only successfully parsed tags are removed; malformed tags remain visible
        cleaned = self.file_ops_parser.edit_pattern.sub("", cleaned)
        cleaned = self.file_ops_parser.create_pattern.sub("", cleaned)
        cleaned = self.file_ops_parser.create_overwrite_pattern.sub("", cleaned)
        cleaned = self.file_ops_parser.delete_pattern.sub("", cleaned)
        cleaned = self.file_ops_parser.move_pattern.sub("", cleaned)
        cleaned = self.file_ops_parser.copy_pattern.sub("", cleaned)
        cleaned = self.file_ops_parser.copy_overwrite_pattern.sub("", cleaned)
        cleaned = self.file_ops_parser.append_pattern.sub("", cleaned)
        cleaned = self.file_ops_parser.insert_after_pattern.sub("", cleaned)
        cleaned = self.file_ops_parser.insert_before_pattern.sub("", cleaned)
        cleaned = self.file_ops_parser.mkdir_pattern.sub("", cleaned)
        cleaned = self.file_ops_parser.rmdir_pattern.sub("", cleaned)
        cleaned = self.file_ops_parser.read_pattern.sub("", cleaned)
        cleaned = self.file_ops_parser.grep_pattern.sub("", cleaned)

        # Strip plugin-registered tags
        for tag in self._plugin_tags:
            cleaned = tag["pattern"].sub("", cleaned)

        # Clean up excessive whitespace
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = cleaned.strip()

        return cleaned

    def get_all_tools(self, parsed_response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get all tools (terminal + MCP + file ops + plugin) in execution order.

        Args:
            parsed_response: Parsed response from parse_response()

        Returns:
            List of all tools to execute in order
        """
        components = parsed_response.get("components", {})

        all_tools = []
        all_tools.extend(components.get("terminal_commands", []))
        all_tools.extend(components.get("tool_calls", []))
        all_tools.extend(components.get("file_operations", []))
        all_tools.extend(components.get("plugin_tools", []))

        # Sort by original position in response text to preserve
        # the order the LLM intended (create before read before edit, etc.)
        all_tools.sort(key=lambda t: t.get("_position", float("inf")))
        return all_tools

    def format_for_display(
        self, parsed_response: Dict[str, Any], show_thinking: bool = True
    ) -> str:
        """Format parsed response for terminal display.

        Args:
            parsed_response: Parsed response data
            show_thinking: Whether to include thinking content

        Returns:
            Formatted string for display
        """
        parts = []

        # Add thinking content if enabled
        if show_thinking:
            thinking = parsed_response.get("components", {}).get("thinking", [])
            for thought in thinking:
                parts.append(f"[dim]{thought}[/dim]")
                parts.append("")

        # Add main content
        content = parsed_response.get("content", "").strip()
        if content:
            parts.append(content)

        # Add tool execution indicators
        metadata = parsed_response.get("metadata", {})
        if (
            metadata.get("has_terminal_commands")
            or metadata.get("has_tool_calls")
            or metadata.get("has_file_operations")
        ):
            tools_count = metadata.get("total_tools", 0)
            parts.append("")
            parts.append(f"[cyan]Executing {tools_count} tool(s)...[/cyan]")

        return "\n".join(parts)

    def validate_response(self, response: str) -> Tuple[bool, List[str]]:
        """Validate response format and syntax.

        Args:
            response: Raw response to validate

        Returns:
            Tuple of (is_valid, list_of_issues)
        """
        issues = []

        # Check for unclosed tags
        open_tags = ["<think>", "<terminal>", "<tool"]
        close_tags = ["</think>", "</terminal>", "</tool>"]

        for open_tag, close_tag in zip(open_tags, close_tags):
            if open_tag in response and close_tag not in response:
                issues.append(f"Unclosed tag: {open_tag}")

        # Check for malformed tool tags
        tool_matches = self.tool_pattern.findall(response)
        for attributes_str, content in tool_matches:
            if "name=" not in attributes_str:
                issues.append("Tool tag missing 'name' attribute")

        # Check for empty response
        if not response.strip():
            issues.append("Empty response")

        return len(issues) == 0, issues

    def extract_execution_stats(
        self, parsed_response: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract execution statistics from parsed response.

        Args:
            parsed_response: Parsed response data

        Returns:
            Execution statistics
        """
        metadata = parsed_response.get("metadata", {})
        components = parsed_response.get("components", {})

        return {
            "content_words": len(parsed_response.get("content", "").split()),
            "thinking_blocks": len(components.get("thinking", [])),
            "terminal_commands": len(components.get("terminal_commands", [])),
            "mcp_tool_calls": len(components.get("tool_calls", [])),
            "total_tools": metadata.get("total_tools", 0),
            "turn_completed": parsed_response.get("turn_completed", True),
            "complexity": self._assess_complexity(parsed_response),
        }

    def _assess_complexity(self, parsed_response: Dict[str, Any]) -> str:
        """Assess response complexity level.

        Args:
            parsed_response: Parsed response data

        Returns:
            Complexity level: simple, moderate, complex
        """
        score = 0
        metadata = parsed_response.get("metadata", {})

        # Content length scoring
        content_length = metadata.get("content_length", 0)
        if content_length > 500:
            score += 2
        elif content_length > 200:
            score += 1

        # Tool usage scoring
        if metadata.get("has_thinking"):
            score += 1
        if metadata.get("has_terminal_commands"):
            score += 1
        if metadata.get("has_tool_calls"):
            score += 2

        # Multiple tools indicate complexity
        if metadata.get("total_tools", 0) > 1:
            score += 1

        # Map score to complexity
        if score >= 4:
            return "complex"
        elif score >= 2:
            return "moderate"
        else:
            return "simple"
