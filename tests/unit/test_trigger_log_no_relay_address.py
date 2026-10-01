"""The hub wake log never carries a relay: address (it holds a device key)."""

import asyncio
import logging
from types import SimpleNamespace

from kollabor.llm.message_handler import MessageHandler


def test_trigger_log_redacts_a_relay_address(caplog):
    handler = object.__new__(MessageHandler)
    handler._coordinator = SimpleNamespace(renderer=SimpleNamespace(pipe_mode=True))
    address = "relay:" + "ab" * 32 + ":workspace:agent"
    with caplog.at_level(logging.INFO, logger="kollabor.llm.message_handler"):
        result = asyncio.run(handler.handle_llm_continue({"source": f"hub:{address}"}, None))
    assert result == {"status": "pipe_mode"}
    text = caplog.text
    assert "Received from hub:relay:<address>" in text
    assert "ab" * 32 not in text
