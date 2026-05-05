"""Agent Orchestrator Plugin for Kollab.

This plugin enables the LLM to spawn and manage parallel kollab sub-agents
via XML commands in its responses.

Features:
- Spawn agents: <agent><name><task>...</task><files>...</files></name></agent>
- Message agents: <message to="name">content</message>
- Stop agents: <stop>name</stop>
- Check status: <status />
- Capture output: <capture>name 200</capture>
- Clone with context: <clone>...</clone>
- Team lead: <team lead="name" workers="N">...</team>
- Broadcast: <broadcast to="pattern">content</broadcast>

Sub-agents run as subprocesses and are monitored for completion via MD5 hashing.
When an agent's output stops changing (idle for 6 seconds), it's considered complete
and the output is injected back into the conversation.
"""

from .activity_monitor import ActivityMonitor
from .file_attacher import FileAttacher
from .message_injector import MessageInjector
from .models import AgentSession, AgentTask, ParsedCommand
from .orchestrator import AgentOrchestrator
from .plugin import AgentOrchestratorPlugin
from .ring_buffer import RingBuffer
from .xml_parser import XMLCommandParser

__all__ = [
    "AgentOrchestratorPlugin",
    "AgentTask",
    "AgentSession",
    "ParsedCommand",
    "XMLCommandParser",
    "AgentOrchestrator",
    "ActivityMonitor",
    "MessageInjector",
    "FileAttacher",
    "RingBuffer",
]
