"""Auto-task trigger reliability (issue #211 addendum).

2026-10-16 incident: koordinator made 8 hub_msg handoffs + 8 hub_queue
items; ZERO TaskLedger cards appeared (sapphire's vault confirmed). Root
causes found and fixed here:

1. The hub_msg tag schema could not carry a task_id, so the
   deterministic assignment path (metadata.task_assignment via task_id)
   was unreachable. -> hub_msg now accepts an optional task_id attribute.
2. The heuristic marker list missed koordinator's actual phrasings
   ("New task (Marco via operator): ...", "Next assignment: ...").
   -> intent-level directive openings added; assignment-shaped hold/
   status messages ("Next assignment: nothing queued — hold.") are
   excluded; bare request verbs stay excluded (the 7e24ff49 lesson).
3. Untagged heuristic matches now mint OPEN cards (claimable,
   supervisor-visible) instead of force-assigning 'active' work.

Positive cases below use today's REAL handoff texts verbatim; negative
cases reproduce the historical false-positive shapes.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub.plugin import HubPlugin
from plugins.hub.task_ledger import TaskLedger

# --- today's real handoff texts (session log 2610152245-data-burst) -----

# NOTE on the incident's remaining two handoffs (peridot guard-rail,
# bismuth scope addition): both are follow-ups to ALREADY-tracked tasks,
# so the correct outcome is "wake, but mint no new card" — they are in
# NON_HANDOFFS below. Minting a duplicate card for a follow-up would be
# its own pollution bug.
REAL_HANDOFFS = {
    "zircon_tool_schema": (
        "New task (Marco via operator): fix the top two unwired-gaps issues. "
        "Branch off origin/main @ 8dbdf9fb in a fresh scratch clone (e.g. "
        "/tmp/kollab-tool-schema-fix), branch name your call (e.g. "
        "fix/native-tool-schema-refresh). (1) Issue #202: wire the schema "
        "refresh into _execute_tool_load (tool_executor.py:1703) and "
        "_load_mcp_tool."
    ),
    "lapis_tauri": (
        "New task (Marco via operator): make the publish workflow's Tauri "
        "CLI install use a prebuilt or cached binary so Intel Mac "
        "(macos-x86_64) desktop builds stop taking 30+ minutes. Evidence: "
        "the 'Install pinned Tauri CLI' step took ~15.5 min on the v0.16.2 "
        "desktop/macos-x86_64 run."
    ),
    "sapphire_qa_slot": (
        "New task (sweep findings filed as issues #202-#207 — nice work). "
        "Both named follow-ups (restructure, prompts page) are taken, so "
        "you get the QA slot on the shared artifact: when "
        "/tmp/prompt-page-contract.md appears (aquamarine is writing it), "
        "independently verify it against the source."
    ),
    "lapis_backlog_206": (
        "Next assignment: pick up two backlog ledger items (they're in "
        "hub_queue — work them in this order). (1) Item 6d10d3be / issue "
        "#206: remove the test-only native_json generate_openai_tools and "
        "point test_tool_registry_parity at the real generator."
    ),
    "zircon_hookadapter": (
        "Next assignment: ledger item 641cd3d4 / issue #205 — HookAdapter "
        "in packages/kollabor-events (hook_adapter.py, exported "
        "__init__.py:19, zero consumers). Branch off origin/main @ "
        "8dbdf9fb (e.g. fix/remove-dead-hook-adapter, fresh scratch clone "
        "if yours is dirty)."
    ),
    "explicit_take_ownership": "take ownership of the release verification lane",
}

# Hold/status messages that USE assignment-shaped openings but hand off
# nothing (same incident, same author).
NON_HANDOFFS = {
    "peridot_guard_rail_followup": (
        "Guard rail before you start: your spawn cwd is /Users/malmazan/dev/"
        "kollab — the shared checkout mid-flight. Do NOT read/edit/commit "
        "there. Work exclusively in your scratch clone /tmp/kollab-taskledger "
        "(git clone https://github.com/kollaborai/kollab.git, branch "
        "feat/task-ledger-idle-supervisor off origin/main @ 8dbdf9fb). Task "
        "reminder: issue #211 end to end — durable TaskLedger, hub idle "
        "supervisor, stall detection, koordinator prompt ledger-check "
        "directive, deterministic tests incl. restart recovery and the "
        ">10min-idle acceptance test. Draft PR, no merge. Ack when your "
        "clone exists."
    ),
    "bismuth_scope_addition_followup": (
        "Status check + one scope addition from Marco (via operator): (1) "
        "your branch feat/static-system-prompt @ 1dea372c is pushed and the "
        "suite is still running — good, keep going. (2) ADDITION: put the "
        "lazy MCP summaries at the END of the static prompt (they were "
        "previously 'keep as-is'; the operator now wants them relocated to "
        "the tail of the static section). Everything else in your scope is "
        "unchanged. When the suite finishes: confirm pass, apply the "
        "MCP-summary tail placement if not already in, and open the DRAFT "
        "PR (no merge). Report branch + PR link."
    ),
    "hold_nothing_queued": (
        "Next assignment: nothing queued — hold. If peridot or bismuth "
        "report, backlog #204 (mentiko_adapter) is still pending an "
        "operator decision, so don't start it without my word."
    ),
    "hold_position": (
        "New task was assigned to you earlier — hold position until "
        "verification completes."
    ),
    "ack_chatter": "ack, standing by for the next dispatch",
}

# Historical false-positive shapes the 08-01/08-13 crackdowns were for.
OLD_FALSE_POSITIVES = {
    "report_with_fix": "I fixed the bug — prefix matching resolved. All checks pass.",
    "report_shipped": "shipped the capture fix, PR is up for review",
    "bare_question": "can you check the CI status?",
    "status_contains_fix_word": "the config prefix validation now passes",
    "done_report": "task complete. all docs/gate items resolved.",
}


def _shell():
    return object.__new__(HubPlugin)


class TestRealHandoffsClassify:
    """Every 2026-10-16 real handoff must classify as an assignment."""

    @pytest.mark.parametrize("name", sorted(REAL_HANDOFFS))
    def test_real_handoff_is_assignment(self, name):
        shell = _shell()
        assert HubPlugin._is_explicit_task_assignment(
            shell, REAL_HANDOFFS[name]
        ), f"real handoff {name} still invisible to the tracker"

    @pytest.mark.parametrize("name", sorted(NON_HANDOFFS))
    def test_hold_messages_are_not_assignments(self, name):
        shell = _shell()
        assert not HubPlugin._is_explicit_task_assignment(
            shell, NON_HANDOFFS[name]
        ), f"hold/status message {name} wrongly classified as handoff"

    @pytest.mark.parametrize("name", sorted(OLD_FALSE_POSITIVES))
    def test_old_false_positives_stay_out(self, name):
        shell = _shell()
        assert not HubPlugin._is_explicit_task_assignment(
            shell, OLD_FALSE_POSITIVES[name]
        ), f"historical false positive {name} regressed"


class TestTaggedHandoff:
    """task_id on hub_msg makes classification and minting deterministic."""

    def test_task_id_alone_makes_assignment(self):
        shell = _shell()
        assert HubPlugin._is_explicit_task_assignment(
            shell,
            "please handle the restart-recovery suite",
            task_id="548b2bd3",
        )

    def test_hub_msg_tag_schema_accepts_task_id(self):
        from plugins.hub.xml_tags import tag_attrs, tag_pattern

        pat = tag_pattern(
            "hub_msg",
            required={"to": "some"},
            valid={"task_id": r"[0-9A-Za-z._:@-]+"},
            end="body?",
        )
        m = pat.search(
            '<hub_msg to="lapis" task_id="548b2bd3">directive text' "</hub_msg>"
        )
        assert m, "task_id attribute broke hub_msg parsing"
        attrs = tag_attrs(m.group(1))
        assert attrs.get("to") == "lapis"
        assert attrs.get("task_id") == "548b2bd3"
        assert m.group(2) == "directive text"

    def test_hub_msg_tag_schema_rejects_bad_task_id(self):
        from plugins.hub.xml_tags import tag_attrs, tag_pattern

        pat = tag_pattern(
            "hub_msg",
            required={"to": "some"},
            valid={"task_id": r"[0-9A-Za-z._:@-]+"},
            end="body?",
        )
        assert not pat.search('<hub_msg to="lapis" task_id="a b">x</hub_msg>')
        # Messages without task_id parse exactly as before.
        m = pat.search('<hub_msg to="lapis">plain message</hub_msg>')
        assert m and tag_attrs(m.group(1)).get("task_id", "") == ""


def _make_receiver(tmp_path, identity="lapis"):
    from pathlib import Path as _Path

    tmp_path = _Path(tmp_path)
    plugin = object.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(
        identity=identity,
        agent_id=f"{identity}-001",
        is_coordinator=False,
        project="proj",
    )
    plugin._task_ledger = TaskLedger(tasks_dir=str(tmp_path / "tasks"))
    plugin._work_queue = None
    plugin._presence = None
    plugin._roster = []
    plugin._vault = None
    plugin._supervisor_state = SimpleNamespace(nudges={})
    plugin._supervisor_task = None
    plugin._seen_messages = {}
    plugin._seen_content_hashes = {}
    plugin.event_bus = None
    plugin._correlation = None
    plugin._bridge = None
    plugin._active_thread_id = ""
    plugin._active_thread_msg_id = ""
    plugin._display_hub_message = lambda message: None
    plugin._handle_qa_needed_message = AsyncMock()
    plugin._handle_task_cron_message = AsyncMock()
    return plugin


def _msg(to, content, from_identity="koordinator", metadata=None):
    from plugins.hub.models import HubMessage, MessageScope

    return HubMessage(
        action="message",
        from_agent=f"{from_identity}-001",
        from_identity=from_identity,
        to=to,
        content=content,
        scope=MessageScope.DIRECT.value,
        metadata=metadata or {},
    )


class TestReceiverMint:
    """The inbound mint block: tagged -> active card, untagged -> OPEN."""

    def test_real_untagged_handoff_mints_open_card(self, tmp_path):
        """The incident's core failure, end to end: koordinator's real
        'New task' handoff must now produce a durable ledger card."""
        plugin = _make_receiver(tmp_path, identity="zircon")
        message = _msg(
            "zircon",
            REAL_HANDOFFS["zircon_tool_schema"],
            metadata={"task_assignment": True},  # sender classified it
        )
        asyncio.new_event_loop().run_until_complete(
            plugin._on_message_received(message)
        )
        # Untagged -> OPEN, claimable, supervisor-visible.
        open_cards = plugin._task_ledger.get_open()
        assert len(open_cards) == 1
        card = open_cards[0]
        assert card.assigner == "koordinator"
        assert "New task (Marco via operator)" in card.directive
        assert card.status == "open"
        # And the assignee's name is in the directive, since the card sat
        # in the shared pool.
        assert card.directive.startswith("zircon:")

    def test_tagged_handoff_mints_active_card_with_task_id(self, tmp_path):
        plugin = _make_receiver(tmp_path, identity="zircon")
        message = _msg(
            "zircon",
            "please handle the restart-recovery suite",
            metadata={"task_assignment": True, "task_id": "548b2bd3"},
        )
        asyncio.new_event_loop().run_until_complete(
            plugin._on_message_received(message)
        )
        card = plugin._task_ledger.get("548b2bd3")
        assert card is not None
        assert card.status == "active"
        assert card.assignee == "zircon"

    def test_tagged_duplicate_not_reminted(self, tmp_path):
        plugin = _make_receiver(tmp_path, identity="zircon")
        payload = {
            "task_assignment": True,
            "task_id": "548b2bd3",
        }
        loop = asyncio.new_event_loop()
        for _ in range(2):
            loop.run_until_complete(
                plugin._on_message_received(
                    _msg("zircon", "handle it", metadata=dict(payload))
                )
            )
        loop.close()
        assert plugin._task_ledger.get("548b2bd3") is not None
        open_cards = plugin._task_ledger.get_open()
        assert open_cards == []  # no duplicate pool entry

    def test_cron_echo_never_mints(self, tmp_path):
        plugin = _make_receiver(tmp_path, identity="zircon")
        message = _msg(
            "zircon",
            "[task reminder] new task: finish the thing",
            from_identity="task-cron",
        )
        asyncio.new_event_loop().run_until_complete(
            plugin._on_message_received(message)
        )
        assert plugin._task_ledger.get_open() == []

    def test_untagged_handoff_survives_receiver_restart(self, tmp_path):
        """Durability: the minted OPEN card is plain JSON on disk, so a
        hub restart (fresh TaskLedger over the same dir) keeps it — the
        property the issue's acceptance criterion depends on."""
        plugin = _make_receiver(tmp_path, identity="zircon")
        message = _msg(
            "zircon",
            REAL_HANDOFFS["lapis_tauri"],
            metadata={"task_assignment": True},
        )
        asyncio.new_event_loop().run_until_complete(
            plugin._on_message_received(message)
        )
        fresh = TaskLedger(tasks_dir=str(tmp_path / "tasks"))
        assert len(fresh.get_open()) == 1
