"""Application-owned ACP runner orchestration.

This module adapts configured ACP profiles to the existing event/history seam;
ACP transport and permission protocol details remain in ``acp_bridge``.
"""
from __future__ import annotations

import logging
from typing import Any

from kollabor_ai.profile_manager import LLMProfile

from .acp_bridge import ACPProfile, ACPResult, ACPRunner

logger = logging.getLogger(__name__)


class ACPOrchestrator:
    """Own one ACP runner for one daemon identity/profile."""

    def __init__(self, event_bus: Any, *, identity: str = "default", hub_plugin: Any = None) -> None:
        self.event_bus = event_bus
        self.identity = identity
        self.hub_plugin = hub_plugin
        self._binding: tuple[str, int] | None = None
        self._runner: ACPRunner | None = None
        self._profile_name: str | None = None
        self._closed = False

    @property
    def active(self) -> bool:
        return self._runner is not None and not self._closed

    @property
    def runner(self) -> ACPRunner | None:
        return self._runner

    async def start(self, profile: LLMProfile) -> None:
        if profile.provider != "acp":
            raise ValueError(f"profile {profile.name!r} is not an ACP profile")
        if not profile.command:
            raise ValueError(f"ACP profile {profile.name!r} has no command")
        if self._runner is not None:
            raise RuntimeError("ACP runner already started for this identity")

        command = tuple(profile.command)
        mcp_servers = []
        if self.hub_plugin is not None:
            token, generation = self.hub_plugin.rotate_acp_hub_binding()
            self._binding = (token, generation)
            socket_path = getattr(getattr(self.hub_plugin, "_socket_server", None), "socket_path", "")
            from kollabor.mcp_hub import kollab_hub_argv
            mcp_servers.append({
                "name": "kollab-hub",
                "command": kollab_hub_argv()[0],
                "args": kollab_hub_argv()[1:],
                "env": {
                    "KOLLAB_ACP_HUB_TOKEN": token,
                    "KOLLAB_ACP_HUB_GENERATION": str(generation),
                    "KOLLAB_ACP_HUB_SOCKET": str(socket_path),
                },
            })
        acp_profile = ACPProfile(
            name=profile.name,
            command=command,
            cwd=profile.cwd or profile.workspace or None,
            workspace=profile.workspace or None,
            pass_env=profile.pass_env,
            mcp_servers=tuple(mcp_servers),
        )
        self._runner = ACPRunner(acp_profile)
        self._profile_name = profile.name
        await self._runner.start()
        self._closed = False
        self.event_bus.register_service("acp_orchestrator", self)

    async def submit(self, text: str, *, source: str = "rpc") -> dict[str, Any]:
        if self._runner is None or self._closed:
            raise RuntimeError("ACP runner is not active")
        result: ACPResult = await self._runner.prompt(text)
        tap = self.event_bus.get_service("display_tap")
        if tap is not None:
            for update in result.updates:
                tap.publish(update)
            tap.publish_semantic(
                {
                    "type": "turn_complete",
                    "session_id": result.session_id,
                    "generation": result.generation,
                    "turn_id": result.turn_id,
                    "stop_reason": result.stop_reason,
                    "source": source,
                }
            )
        return {
            "text": result.text,
            "session_id": result.session_id,
            "generation": result.generation,
            "turn_id": result.turn_id,
            "stop_reason": result.stop_reason,
            "end_frame": result.end_frame,
        }

    async def cancel(self) -> None:
        if self._runner is not None and not self._closed:
            await self._runner.cancel()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._runner is not None:
            await self._runner.close()
            self._runner = None
        if self.hub_plugin is not None and self._binding is not None:
            self.hub_plugin.rotate_acp_hub_binding()
            self._binding = None
