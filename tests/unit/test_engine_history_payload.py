import asyncio

from kollabor_engine.session import EngineSession

from kollabor.state.snapshots import MessageDto


class _FakeState:
    async def get_conversation(self, since=None, anchor=None):
        return type("Snapshot", (), {
            "messages": [
                MessageDto(
                    role="assistant",
                    content="working",
                    timestamp="2026-08-24T00:00:00",
                    metadata={"tool_calls": [{"id": "call-1"}]},
                    thinking="trace",
                )
            ]
        })()


class _FakeDaemon:
    state = _FakeState()


def test_engine_history_keeps_message_metadata_and_thinking() -> None:
    async def exercise() -> None:
        session = EngineSession.__new__(EngineSession)
        session.daemon = _FakeDaemon()
        session.history = []
        session._raw_history = []
        session._history_anchor = ""
        session._history_lock = asyncio.Lock()

        history = await session.refresh_history()

        assert history == [
            {
                "role": "assistant",
                "content": "working",
                "timestamp": "2026-08-24T00:00:00",
                "metadata": {"tool_calls": [{"id": "call-1"}]},
                "thinking": "trace",
            }
        ]

    asyncio.run(exercise())
