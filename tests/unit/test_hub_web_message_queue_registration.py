"""Hub lifecycle registration for the safe daemon queue management surface."""

from __future__ import annotations

from kollabor.state.web_message_queue_rpc import WebMessageQueueRegistry
from kollabor_rpc import RpcServer
from plugins.hub.plugin import HubPlugin


class _ConversationState:
    def get_conversation_uid(self) -> str:
        return "conversation-a"


def test_hub_composes_queue_rpcs_on_its_authenticated_rpc_server():
    plugin = HubPlugin()
    plugin._rpc_server = RpcServer()

    registered = plugin._register_web_message_queue_runtime(_ConversationState())

    assert registered is True
    assert isinstance(plugin._web_message_queue_runtime.registry, WebMessageQueueRegistry)
    assert plugin._web_message_queue_runtime.dispatch_available is False
    methods = set(plugin._rpc_server.list_methods())
    assert "state.web_queue.snapshot" in methods
    assert "state.web_queue.enqueue_batch" in methods
    assert "state.web_queue.stage_attachment" in methods
    assert "state.web_queue.claim_batch" not in methods


def test_hub_leaves_queue_unregistered_when_rpc_server_or_state_is_missing():
    plugin = HubPlugin()

    assert plugin._register_web_message_queue_runtime(_ConversationState()) is False
    plugin._rpc_server = RpcServer()
    assert plugin._register_web_message_queue_runtime(None) is False
    assert "state.web_queue.snapshot" not in plugin._rpc_server.list_methods()
