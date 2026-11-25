"""The terminal bridge sends keystrokes that pile up behind a slow daemon call as one call, not one each."""

import asyncio
import json

import pytest

pytest.importorskip("fastapi")

from kollabor_engine.routes import code  # noqa: E402


class _Ws:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []

    async def receive_text(self):
        if not self._messages:
            await asyncio.sleep(0.2)
            raise code.WebSocketDisconnect()
        return self._messages.pop(0)

    async def send_json(self, message):
        self.sent.append(message)


class _State:
    def __init__(self):
        self.calls = []

    async def code_call(self, op, params):
        self.calls.append(json.loads(params["raw"]))
        await asyncio.sleep(0.05)  # a slow link: keys typed meanwhile queue up
        return {"status": 200, "body": {"data": {"messages": []}}}


class _Session:
    def __init__(self):
        self.state = _State()


def _input(text):
    return json.dumps({"type": "input", "data": text})


def test_keys_typed_during_a_slow_call_go_out_as_one_input():
    ws = _Ws([_input(c) for c in "hello world"])
    session = _Session()
    with pytest.raises(code.WebSocketDisconnect):
        asyncio.run(code._forward(ws, session, "link-12345678", asyncio.Event()))
    sent = "".join(m["data"] for m in session.state.calls if m["type"] == "input")
    assert sent == "hello world"
    assert len(session.state.calls) <= 2


def test_other_messages_keep_their_order_between_inputs():
    resize = json.dumps({"type": "resize", "cols": 80, "rows": 24})
    ws = _Ws([_input("a"), _input("b"), resize, _input("c")])
    session = _Session()
    with pytest.raises(code.WebSocketDisconnect):
        asyncio.run(code._forward(ws, session, "link-12345678", asyncio.Event()))
    assert [m["type"] for m in session.state.calls] == ["input", "resize", "input"]
    assert [m.get("data") for m in session.state.calls if m["type"] == "input"] == [
        "ab",
        "c",
    ]
