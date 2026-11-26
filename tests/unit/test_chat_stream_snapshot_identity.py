from datetime import datetime, timezone
from types import SimpleNamespace

from kollabor.state.chat_stream import ChatStreamService
from kollabor_events.data_models import ConversationMessage


class _Bus:
    def get_service(self, _name):
        return None

    def register_service(self, _name, _service):
        pass


class _State:
    def __init__(self, history):
        self._event_bus = _Bus()
        self._llm_service = SimpleNamespace(
            conversation_manager=None,
            conversation_history=history,
        )

    def get_conversation_uid(self):
        return "conversation-snapshot-identity"

    def chat_stream_snapshot(self):
        return {"rows": [], "turns": [], "inputs": []}


def test_reply_segments_with_different_request_ids_keep_one_snapshot_row():
    turn_id = "acp-turn-7"
    history_key = "session-turn-key-7"
    request_id = "request-7"
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    history = [
        ConversationMessage(
            role="user",
            content="question",
            timestamp=start,
            metadata={
                "turn_id": turn_id,
                "request_id": request_id,
                "acp_turn_key": history_key,
            },
        ),
        ConversationMessage(
            role="assistant",
            content="first reply segment",
            timestamp=start,
            metadata={"turn_id": turn_id, "request_id": request_id},
        ),
        ConversationMessage(
            role="assistant",
            content="second reply segment",
            timestamp=start,
            metadata={"turn_id": turn_id, "request_id": "follow-up-request"},
        ),
    ]
    stream = ChatStreamService(_State(history))

    snapshot = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]

    replies = [row for row in snapshot["data"]["rows"] if row["kind"] == "reply"]
    expected_id = stream._reply_row_id(
        "conversation-snapshot-identity", turn_id, history_key
    )
    assert [row["id"] for row in replies] == [expected_id]
