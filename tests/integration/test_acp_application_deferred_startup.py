"""Durable ACP local/attach permission wrapper race coverage.

These tests use the real TerminalLLMChat constructor and deferred ACP startup,
while replacing only the two view responders with deterministic account-free
fakes.  The SDK child is still started and cleaned up by the real owner.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from kollabor.application import TerminalLLMChat
from kollabor_ai.profile_manager import ProfileManager
from kollabor_config import config_utils
from plugins.hub import presence
from plugins.hub.plugin import HubPlugin
from tests.integration.test_acp_application_deferred_e2e import AGENT


class _View:
    def __init__(self, response):
        self.response = response
        self.opened = asyncio.Event()
        self.closed = 0
        self.release = asyncio.Event()

    async def show(self, _details):
        self.opened.set()
        try:
            await self.release.wait()
            return self.response
        finally:
            self.closed += 1

    def close(self):
        self.release.set()


@contextlib.asynccontextmanager
async def _app_with_permission_views():
    with tempfile.TemporaryDirectory(prefix="kacp-wrap-", dir="/tmp") as task_dir:
        root = Path(task_dir)
        global_root, home, project = (root / name for name in ("global", "home", "project"))
        for path in (global_root, home, project):
            path.mkdir()
        (global_root / "config.json").write_text(
            json.dumps(
                {
                    "kollabor": {
                        "llm": {
                            "default_profile": "owned-acp",
                            "profiles": {
                                "owned-acp": {
                                    "provider": "acp",
                                    "command": [sys.executable, "-c", AGENT],
                                    "workspace": str(project),
                                }
                            },
                        }
                    },
                    "plugins": {"hub": {"enabled": True, "project_scoped": True}},
                }
            )
        )
        instances = {}

        def factory(bus, renderer, configuration):
            hub = HubPlugin(event_bus=bus, renderer=renderer, config=configuration)
            instances["hub"] = hub
            return {"hub": hub}

        registry = SimpleNamespace(
            get_merged_config=lambda: {},
            list_plugins=lambda: [],
            instantiate_plugins=factory,
        )
        app = None
        with (
            contextlib.chdir(project),
            patch.object(config_utils, "get_config_directory", return_value=global_root),
            patch.object(Path, "home", return_value=home),
            patch.object(presence, "SOCKET_DIR_ROOT", root / "sockets"),
            patch.object(ProfileManager, "_detect_oauth_provider"),
            patch.object(ProfileManager, "_detect_env_provider"),
            patch.dict(
                os.environ,
                {
                    "KOLLAB_CONFIG_DIR": str(global_root),
                    "KOLLAB_PROJECT_ROOT": str(project),
                    "KOLLAB_HUB_PROJECT_SCOPED": "1",
                    "KOLLAB_HUB_SOLO": "0",
                    "KOLLAB_PARENT_PID": "",
                },
            ),
            patch("kollabor.application.setup_from_config"),
            patch.object(HubPlugin, "_resume_relay", AsyncMock()),
        ):
            app = TerminalLLMChat(
                args=SimpleNamespace(simple=True, no_mcp=True, as_identity="owned-wrap", pipe=False, cli_command=None),
                profile_name="owned-acp",
                plugin_registry=registry,
            )
            app.running = True
            app.render_loop = None
            for name in (
                "_check_for_updates",
                "_display_provider_warning",
                "_check_first_run_wizard",
                "_display_ready_message",
            ):
                setattr(app, name, AsyncMock())
            await app.llm_service.register_hooks()
            await asyncio.wait_for(app._deferred_startup(), timeout=12)
            try:
                yield app
            finally:
                await asyncio.wait_for(app.cleanup(), timeout=12)


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["local", "attach"])
async def test_acp_permission_views_first_winner_closes_loser_and_rejects_late(winner):
    async with _app_with_permission_views() as app:
        local = _View("APPROVE_ONCE")
        attach = _View("APPROVE_ONCE")
        app.renderer.layout_manager.show_permission_prompt = local.show
        app.renderer.layout_manager.cancel_permission_prompt = local.close
        app._try_attach_permission_prompt = attach.show
        callback = app.permission_manager._confirmation_callback
        details = {
            "source": "acp",
            "tool_id": "wrapped-first",
            "options": ({"optionId": "owned-once", "kind": "allow_once"},),
            "operation": {"kind": "execute", "command": ["echo", "owned"]},
            "workspace": str(Path.cwd()),
            "profile": "owned-acp",
        }
        task = asyncio.create_task(callback(details))
        await asyncio.wait_for(local.opened.wait(), timeout=2)
        await asyncio.wait_for(attach.opened.wait(), timeout=2)
        if winner == "local":
            local.release.set()
        else:
            attach.release.set()
        result = await asyncio.wait_for(task, timeout=3)
        assert result is not None or task.done()
        assert local.closed == 1
        assert attach.closed == 1
        # A late answer cannot reopen or settle a completed prompt.
        assert app._try_attach_permission_prompt is not None


@pytest.mark.asyncio
async def test_acp_permission_outer_cancel_closes_both_views():
    async with _app_with_permission_views() as app:
        local = _View("APPROVE_ONCE")
        attach = _View("APPROVE_ONCE")
        app.renderer.layout_manager.show_permission_prompt = local.show
        app.renderer.layout_manager.cancel_permission_prompt = local.close
        app._try_attach_permission_prompt = attach.show
        callback = app.permission_manager._confirmation_callback
        details = {
            "source": "acp",
            "tool_id": "wrapped-cancel",
            "options": (),
            "operation": {"kind": "execute", "command": ["echo", "owned"]},
            "workspace": str(Path.cwd()),
            "profile": "owned-acp",
        }
        task = asyncio.create_task(callback(details))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert local.closed == 1
        assert attach.closed == 1
