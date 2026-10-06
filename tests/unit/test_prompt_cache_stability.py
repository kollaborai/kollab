"""Prompt-prefix stability across requests.

Anything that renders differently between requests, ahead of the growing
conversation, makes a prefix-caching provider re-process it. Two producers
were guilty:

- SystemPromptBuilder.build_volatile_context() (the "[context]" blocks the
  queue processor attaches to the newest message) re-rendered on every request.
- HubPlugin._inject_roster_context() rewrote the system message on every
  request with roster state, task timers, lane claims and file changes.
"""

import asyncio
from types import SimpleNamespace

from kollabor_ai.context_service import ContextService
from kollabor_ai.system_prompt_builder import SystemPromptBuilder
from kollabor_events.data_models import ConversationMessage
from plugins.hub.plugin import HubPlugin


class _Bus:
    def __init__(self, **services):
        self.services = services

    def get_service(self, name):
        return self.services.get(name)


# --- "[context]" block -----------------------------------------------------


def _builder(history, renders):
    def render(text, **_kwargs):
        renders.append(text)
        return f"render #{len(renders)} of {text}"

    bus = _Bus(llm_service=SimpleNamespace(conversation_history=history))
    builder = SystemPromptBuilder(
        config=SimpleNamespace(get=lambda _key, default=None: default),
        agent_manager=SimpleNamespace(event_bus=bus),
        util_imports={"render_system_prompt": render},
    )
    return builder


def test_volatile_context_renders_once_per_user_turn():
    history = [
        ConversationMessage(role="system", content="sys"),
        ConversationMessage(role="user", content="first"),
    ]
    renders = []
    builder = _builder(history, renders)

    first = builder.build_volatile_context()
    once = len(renders)
    # each part is its own keyed block, so one changed part re-sends only itself
    keys = [key for key, _ in first]
    assert {"context:hub_roster", "context:hub_vault", "context:active_llm"} <= set(keys)
    assert all(block.startswith("[context]\n") for _, block in first)
    # tool loop: assistant + tool messages land after the user message
    history.append(ConversationMessage(role="assistant", content="calling"))
    assert builder.build_volatile_context() == first
    assert len(renders) == once

    history.append(ConversationMessage(role="user", content="second"))
    second = builder.build_volatile_context()
    assert second != first and len(renders) == 2 * once


def test_volatile_context_without_a_conversation_still_renders_each_call():
    renders = []
    builder = _builder([], renders)
    builder.build_volatile_context()
    once = len(renders)
    builder.build_volatile_context()
    assert once and len(renders) == 2 * once


# --- keyed ephemeral injections ----------------------------------------------


def test_keyed_injection_replaces_an_undrained_one():
    svc = ContextService()
    svc.queue_ephemeral_injection("legacy block")
    svc.queue_ephemeral_injection("hub v1", key="hub_live")
    svc.queue_ephemeral_injection("hub v2", key="hub_live")  # cancelled request
    assert svc.drain_ephemeral_injections() == [("", "legacy block"), ("hub_live", "hub v2")]
    assert svc.drain_ephemeral_injections() == []


# --- hub roster ----------------------------------------------------------------


class _Ledger:
    def get_active_for(self, _identity):
        return [
            SimpleNamespace(
                id="t1",
                assigner="koordinator",
                priority="high",
                directive="ship it",
                deliverable="",
                report_to="koordinator",
                status="active",
                last_checkpoint_note=lambda: "",
                elapsed_str=lambda: "3m",
                is_timed_out=lambda: False,
                is_snoozed=lambda: False,
            )
        ]


class _Feed:
    def get_claims(self):
        return {"claims": {"p": {"identity": "zircon", "path": "a.py", "task": "x"}}}

    def get_subscriptions(self, _identity):
        return []


def _plugin(history, rail):
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(identity="koordinator", is_coordinator=True)
    plugin._roster = [{"identity": "lapis", "state": "idle"}]
    plugin.config = None
    plugin._task_ledger = _Ledger()
    plugin._scratchpad = None
    plugin._vault = None
    plugin._change_feed = _Feed()
    services = {"llm_service": SimpleNamespace(conversation_history=history)}
    if rail is not None:
        services["context_service"] = rail
    plugin.event_bus = _Bus(**services)
    return plugin


class _Rail:
    def __init__(self):
        self.queued = []

    def queue_ephemeral_injection(self, content, key=""):
        self.queued.append((key, content))


def _history():
    return [
        ConversationMessage(role="system", content="base prompt"),
        ConversationMessage(role="user", content="do the thing"),
    ]


def test_system_message_is_stable_while_live_state_rides_the_rail():
    history, rail = _history(), _Rail()
    plugin = _plugin(history, rail)

    asyncio.run(plugin._inject_roster_context({}))
    system_1 = history[0].content
    assert "--- hub context ---" in system_1 and "<hub_msg to=" in system_1
    for volatile in ("lapis - idle", "active tasks", "zircon -> a.py", "elapsed"):
        assert volatile not in system_1
    key, live = rail.queued[-1]
    assert key == "hub_live"
    for state in ("lapis - idle", "elapsed: 3m", "zircon -> a.py"):
        assert state in live

    # state changes mid-turn: system message and rail text are byte-identical
    plugin._roster = [{"identity": "lapis", "state": "working", "current_task": "x"}]
    history.append(ConversationMessage(role="assistant", content="calling"))
    asyncio.run(plugin._inject_roster_context({}))
    assert history[0].content == system_1
    assert rail.queued[-1][1] == live

    # the next user message picks the new state up
    history.append(ConversationMessage(role="user", content="next"))
    asyncio.run(plugin._inject_roster_context({}))
    assert history[0].content == system_1
    assert "lapis - working: x" in rail.queued[-1][1]


def test_without_an_injection_rail_the_live_state_stays_in_the_system_message():
    history = _history()
    plugin = _plugin(history, rail=None)
    asyncio.run(plugin._inject_roster_context({}))
    content = history[0].content
    assert "lapis - idle" in content and "zircon -> a.py" in content
    assert content.count("--- hub context ---") == 1
