"""The engine owns the plugin supervisor task, not the plugin processes."""

import asyncio

import pytest
from fastapi import FastAPI
from kollabor_engine.server import _engine_lifespan


@pytest.mark.asyncio
async def test_engine_starts_and_cancels_supervisor(monkeypatch):
    from kollabor.llm import acp_discovery
    from kollabor_plugins import services

    started = asyncio.Event()
    stopped = asyncio.Event()

    async def supervisor():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def no_warming():
        pass

    monkeypatch.setattr(services, "supervise_services", supervisor)
    monkeypatch.setattr(acp_discovery, "stop_warming", no_warming)
    app = FastAPI()
    async with _engine_lifespan(app):
        await asyncio.wait_for(started.wait(), timeout=1)
    assert stopped.is_set()
