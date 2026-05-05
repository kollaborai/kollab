"""Parse XML agent commands from LLM response text."""

import logging
import re
from typing import List

from .models import AgentTask, ParsedCommand

logger = logging.getLogger(__name__)


class XMLCommandParser:
    """Parse XML agent commands from LLM response text."""

    def parse(self, text: str) -> List[ParsedCommand]:
        """Extract all agent commands from text."""
        # Strip sys_msg blocks - these contain documentation examples that
        # should NOT be executed as actual commands
        _STRIP_SYS_MSG = re.compile(r"<sys_msg>.*?</sys_msg>", re.DOTALL)
        text = _STRIP_SYS_MSG.sub("", text)

        logger.debug(f"Parsing XML commands, input length: {len(text)}")

        commands = []

        # <agent>...</agent>
        commands.extend(self._parse_agent_blocks(text))

        # <message to="name">content</message>
        commands.extend(self._parse_messages(text))

        # <stop>name</stop>
        commands.extend(self._parse_stops(text))

        # <status />
        commands.extend(self._parse_status(text))

        # <capture>name lines</capture>
        commands.extend(self._parse_captures(text))

        # <clone>...</clone>
        commands.extend(self._parse_clones(text))

        # <team>...</team>
        commands.extend(self._parse_teams(text))

        # <broadcast to="pattern">content</broadcast>
        commands.extend(self._parse_broadcasts(text))

        logger.debug(f"Parsed {len(commands)} commands from XML")
        for cmd in commands:
            logger.debug(
                f"  Command: {cmd.type}, agents: {[a.name for a in cmd.agents] if cmd.agents else []}"
            )

        return commands

    def _parse_agent_blocks(self, text: str) -> List[ParsedCommand]:
        """Parse <agent>...</agent> blocks."""
        commands = []
        pattern = r"<agent>(.*?)</agent>"

        for match in re.finditer(pattern, text, re.DOTALL):
            block = match.group(1)
            agents = self._parse_agent_definitions(block)
            if agents:
                commands.append(ParsedCommand(type="agent", agents=agents))

        return commands

    def _parse_agent_definitions(self, block: str) -> List[AgentTask]:
        """Parse individual agent definitions within <agent> block."""
        agents = []

        # Match <name>...</name> pattern (agent name is the tag name)
        # Use negative lookahead to avoid matching reserved tags
        pattern = (
            r"<((?!task|files|file|todo|goal|n\d|agent-type|skill)\w[\w-]*)>(.*?)</\1>"
        )

        logger.debug(f"Parsing agent definitions from block: {block[:200]}...")

        for match in re.finditer(pattern, block, re.DOTALL):
            name = match.group(1)
            content = match.group(2)
            logger.debug(
                f"Parsed agent: {name}, task: {content[:100] if content else 'empty'}..."
            )

            # Extract <agent-type>...</agent-type> (optional)
            agent_type_match = re.search(
                r"<agent-type>(.*?)</agent-type>", content, re.DOTALL
            )
            agent_type = agent_type_match.group(1).strip() if agent_type_match else ""

            # Extract <skill>...</skill> tags (optional, can have multiple)
            skills = [
                s.strip()
                for s in re.findall(r"<skill>(.*?)</skill>", content, re.DOTALL)
            ]

            # Extract <task>...</task>
            task_match = re.search(r"<task>(.*?)</task>", content, re.DOTALL)
            task = task_match.group(1).strip() if task_match else ""

            # Extract <files>...</files>
            files = []
            files_match = re.search(r"<files>(.*?)</files>", content, re.DOTALL)
            if files_match:
                file_pattern = r"<file>(.*?)</file>"
                files = [
                    f.strip() for f in re.findall(file_pattern, files_match.group(1))
                ]

            agents.append(
                AgentTask(
                    name=name,
                    task=task,
                    files=files,
                    agent_type=agent_type,
                    skills=skills,
                )
            )

        return agents

    def _parse_messages(self, text: str) -> List[ParsedCommand]:
        """Parse <message to="name">content</message>."""
        commands = []
        pattern = r'<message\s+to=["\']([^"\']+)["\']>(.*?)</message>'

        for match in re.finditer(pattern, text, re.DOTALL):
            target = match.group(1)
            content = match.group(2).strip()
            commands.append(
                ParsedCommand(type="message", target=target, content=content)
            )

        return commands

    def _parse_stops(self, text: str) -> List[ParsedCommand]:
        """Parse <stop>name</stop> or <stop>name1, name2</stop>."""
        commands = []
        pattern = r"<stop>(.*?)</stop>"

        for match in re.finditer(pattern, text, re.DOTALL):
            content = match.group(1).strip()
            # Split by comma or whitespace
            targets = [t.strip() for t in re.split(r"[,\s]+", content) if t.strip()]
            if targets:
                commands.append(ParsedCommand(type="stop", targets=targets))

        return commands

    def _parse_status(self, text: str) -> List[ParsedCommand]:
        """Parse <status></status> - requires proper open AND close tag."""
        # Must have both opening and closing tag - ignores mentions in prose
        if re.search(r"<status>\s*</status>", text):
            return [ParsedCommand(type="status")]
        return []

    def _parse_captures(self, text: str) -> List[ParsedCommand]:
        """Parse <capture>name [lines]</capture>."""
        commands = []
        pattern = r"<capture>(.*?)</capture>"

        for match in re.finditer(pattern, text, re.DOTALL):
            content = match.group(1).strip()
            parts = content.split()

            target = parts[0] if parts else ""
            lines = 50  # default

            if len(parts) > 1:
                try:
                    lines = int(parts[-1])
                    # If last part is a number, target might be comma-separated names
                    target = " ".join(parts[:-1]).replace(",", "").strip()
                except ValueError:
                    # Last part is not a number, everything is the target
                    target = content

            if target:
                commands.append(
                    ParsedCommand(type="capture", target=target, lines=lines)
                )

        return commands

    def _parse_clones(self, text: str) -> List[ParsedCommand]:
        """Parse <clone>...</clone> blocks."""
        commands = []
        pattern = r"<clone>(.*?)</clone>"

        for match in re.finditer(pattern, text, re.DOTALL):
            block = match.group(1)
            agents = self._parse_agent_definitions(block)
            for agent in agents:
                commands.append(
                    ParsedCommand(type="clone", agents=[agent], conversation=True)
                )

        return commands

    def _parse_teams(self, text: str) -> List[ParsedCommand]:
        """Parse <team lead="name" workers="N">...</team>."""
        commands = []
        pattern = (
            r'<team\s+lead=["\']([^"\']+)["\']\s+workers=["\'](\d+)["\']>(.*?)</team>'
        )

        for match in re.finditer(pattern, text, re.DOTALL):
            lead = match.group(1)
            workers = int(match.group(2))
            content = match.group(3)

            # Parse task content as if it were an agent definition
            agents = self._parse_agent_definitions(f"<{lead}>{content}</{lead}>")

            commands.append(
                ParsedCommand(type="team", lead=lead, workers=workers, agents=agents)
            )

        return commands

    def _parse_broadcasts(self, text: str) -> List[ParsedCommand]:
        """Parse <broadcast to="pattern">content</broadcast>."""
        commands = []
        pattern = r'<broadcast\s+to=["\']([^"\']+)["\']>(.*?)</broadcast>'

        for match in re.finditer(pattern, text, re.DOTALL):
            pat = match.group(1)
            content = match.group(2).strip()
            commands.append(
                ParsedCommand(type="broadcast", pattern=pat, content=content)
            )

        return commands
