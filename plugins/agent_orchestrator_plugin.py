"""Agent Orchestrator Plugin - wrapper for plugin discovery.

This file exists to enable plugin discovery which looks for *_plugin.py files.
The actual implementation is in the agent_orchestrator/ directory.
"""

from plugins.agent_orchestrator.plugin import AgentOrchestratorPlugin

__all__ = ["AgentOrchestratorPlugin"]
