"""Data models for agent orchestration."""

import subprocess
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .ring_buffer import RingBuffer


@dataclass
class AgentTask:
    """Parsed agent task from XML."""

    name: str
    task: str
    files: List[str] = field(default_factory=list)
    agent_type: str = ""  # Optional agent type to use (e.g., "coder", "research")
    skills: List[str] = field(default_factory=list)  # Optional skills to load


@dataclass
class AgentSession:
    """Running agent session."""

    name: str
    full_name: str  # Full agent session name
    status: str  # initializing, running, idle, error, stopped
    start_time: float
    proc: Optional[subprocess.Popen] = None
    ring_buffer: Optional["RingBuffer"] = None
    pid: int = 0

    @property
    def duration(self) -> str:
        """Get formatted duration since start."""
        elapsed = time.time() - self.start_time
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        return f"{minutes}m{seconds:02d}s"

    @property
    def is_alive(self) -> bool:
        """Check if the subprocess is still running."""
        if self.proc is None:
            return False
        return self.proc.poll() is None


@dataclass
class ParsedCommand:
    """Parsed XML command from LLM response."""

    type: str  # agent, message, stop, status, capture, clone, team, broadcast
    agents: List[AgentTask] = field(default_factory=list)
    target: str = ""
    targets: List[str] = field(default_factory=list)
    content: str = ""
    lines: int = 50
    pattern: str = ""
    lead: str = ""
    workers: int = 3
    conversation: bool = False
