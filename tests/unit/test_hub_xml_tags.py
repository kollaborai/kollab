"""Hub XML tags take their attributes in any order and in either quote style.

The hub used to register every tag with a regex that spelled the attributes out
in one fixed order, so `<hub_msg wait="true" to="lapis">` matched nothing: the
tag was neither run nor hidden and sat in the reply as raw text. These tests pin
the fix at three levels: the helper (`plugins/hub/xml_tags.py`), every
multi-attribute tag the hub registers, and the real response parser.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_ai.response_parser import ResponseParser
from plugins.hub.plugin import HubPlugin
from plugins.hub.xml_tags import embedded_attrs, tag_attrs, tag_pattern


def _registered():
    """{tag name: (pattern, extractor)} exactly as the hub registers them."""
    parser = SimpleNamespace(register_plugin_tag=MagicMock())
    executor = SimpleNamespace(register_plugin_handler=MagicMock())
    bus = MagicMock()
    bus.get_service.side_effect = {
        "response_parser": parser,
        "tool_executor": executor,
    }.get
    HubPlugin(event_bus=bus)._register_pipeline_tools()
    return {
        call.args[0]: (call.args[1], call.args[3])
        for call in parser.register_plugin_tag.call_args_list
    }


REGISTERED = _registered()

# The tags that take two or more attributes: the canonical tag text with an
# {attrs} slot, every attribute it takes, and what the extractor must return.
# The expected values are what the fixed-order regexes returned for the same
# tag written in their one order.
CONVERTED = {
    "hub_msg": (
        "<hub_msg {attrs}>hello there</hub_msg>",
        {
            "to": "lapis",
            "wait": "true",
            "force": "true",
            "thread_id": "t-1",
            "reply_to": "m-1",
            "kind": "answer",
        },
        {
            "target": "lapis",
            "wait_attr": "true",
            "force_attr": "true",
            "thread_id": "t-1",
            "reply_to": "m-1",
            "kind": "answer",
            "content": "hello there",
        },
    ),
    "hub_reply": (
        "<hub_reply {attrs}>on it</hub_reply>",
        {"to": "lapis", "wait": "true"},
        {
            "target": "lapis",
            "wait_attr": "true",
            "content": "on it",
            "_is_reply": True,
        },
    ),
    "hub_broadcast": (
        "<hub_broadcast {attrs}>all hands</hub_broadcast>",
        {"force": "true", "scope": "network"},
        {"content": "all hands", "force_attr": "true", "scope": "network"},
    ),
    "task_snooze": (
        "<task_snooze {attrs}/>",
        {"id": "abc12345", "minutes": "30"},
        {"task_id": "abc12345", "minutes": "30"},
    ),
    "hub_spawn": (
        "<hub_spawn {attrs}>do it</hub_spawn>",
        {"name": "lapis", "type": "research"},
        {"name": "lapis", "agent_type_override": "research", "task": "do it"},
    ),
    "hub_capture": (
        "<hub_capture {attrs}/>",
        {"name": "lapis", "lines": "20"},
        {"cap_name": "lapis", "cap_lines": "20"},
    ),
    "crystal_search": (
        "<crystal_search {attrs}/>",
        {"query": "some words", "limit": "3"},
        {"query": "some words", "limit": 3},
    ),
    "crystal_list": (
        "<crystal_list {attrs}/>",
        {"limit": "3", "offset": "2"},
        {"limit": 3, "offset": 2},
    ),
    "crystal_edit": (
        "<crystal_edit {attrs}>new body</crystal_edit>",
        {"id": "crys-003", "summary": "short", "keywords": "a, b"},
        {
            "entry_id": "crys-003",
            "content": "new body",
            "summary": "short",
            "keywords": ["a", "b"],
        },
    ),
    "crystal_delete": (
        "<crystal_delete {attrs}/>",
        {"id": "crys-003", "reason": "stale"},
        {"entry_id": "crys-003", "reason": "stale"},
    ),
    "curate": (
        "<curate {attrs}>kept text</curate>",
        {"id": "ctx-1", "decision": "keep"},
        {"ctx_id": "ctx-1", "decision": "keep", "body": "kept text"},
    ),
    "hub_ask_ctx": (
        "<hub_ask_ctx {attrs}/>",
        {"peer": "lapis", "filter": "file:kollabor/"},
        {"peer": "lapis", "filter": "file:kollabor/"},
    ),
    "hub_cron_add": (
        "<hub_cron_add {attrs}>check the tunnel</hub_cron_add>",
        {"interval": "5m", "to": "infra@home-server"},
        {
            "interval": "5m",
            "target": "infra@home-server",
            "message": "check the tunnel",
        },
    ),
}

# The tags that take one attribute or none. A new tag has to be put in one of
# the two lists, so a second attribute cannot slip in behind a fixed-order regex.
ONE_OR_NO_ATTRIBUTE = set("""
    hub_stop hub_status hub_restart scratchpad scratchpad_append scratchpad_clear
    scratchpad_get state_update task_checkpoint task_complete task_approve
    task_reject lane_claim lane_release file_changed file_watch file_unwatch
    feed_recent feed_file claims hub_agents hub_queue hub_claim hub_work hub_vault
    hub_vaults hub_cron_list hub_cron_delete vault_write
    global_vault_write crystal_read context_query evict
    """.split())


def _attrs(pairs, quote='"'):
    return " ".join(f"{name}={quote}{value}{quote}" for name, value in pairs)


def _text(tag, pairs, quote='"'):
    return CONVERTED[tag][0].format(attrs=_attrs(pairs, quote))


def _extract(tag, text):
    pattern, extract = REGISTERED[tag]
    match = pattern.search(text)
    return None if match is None else extract(match)


def test_every_registered_tag_is_classified_as_converted_or_one_attribute():
    unclassified = set(REGISTERED) - set(CONVERTED) - ONE_OR_NO_ATTRIBUTE
    assert not unclassified, (
        f"new hub tag(s) {sorted(unclassified)}: a tag with two or more attributes "
        "must be built with tag_pattern (plugins/hub/xml_tags.py) and added to "
        "CONVERTED; one with a single attribute goes in ONE_OR_NO_ATTRIBUTE"
    )
    assert set(CONVERTED) <= set(REGISTERED)
    assert set(ONE_OR_NO_ATTRIBUTE) <= set(REGISTERED)


@pytest.mark.parametrize("tag", sorted(CONVERTED))
def test_canonical_order_returns_the_same_values_as_before(tag):
    _, attributes, expected = CONVERTED[tag]

    assert _extract(tag, _text(tag, attributes.items())) == expected


@pytest.mark.parametrize("quote", ['"', "'"])
@pytest.mark.parametrize("tag", sorted(CONVERTED))
def test_every_attribute_order_returns_the_same_values(tag, quote):
    _, attributes, expected = CONVERTED[tag]

    orders = list(itertools.permutations(attributes.items()))
    for order in orders:
        text = _text(tag, order, quote)
        assert _extract(tag, text) == expected, text
    assert len(orders) >= 2


@pytest.mark.parametrize("tag", sorted(CONVERTED))
def test_a_subset_of_the_attributes_in_any_order_still_matches(tag):
    _, attributes, _ = CONVERTED[tag]
    required = {
        "hub_msg": {"to"},
        "hub_reply": {"to"},
        "task_snooze": {"id"},
        "hub_spawn": {"name"},
        "hub_capture": {"name"},
        "crystal_search": {"query"},
        "curate": {"id", "decision"},
        "hub_ask_ctx": {"peer"},
    }.get(tag, set())

    for size in range(len(attributes) + 1):
        for subset in itertools.permutations(attributes.items(), size):
            text = _text(tag, subset)
            matched = _extract(tag, text) is not None
            assert matched == required.issubset(dict(subset)), text


@pytest.mark.parametrize(
    "tag, missing",
    [
        ("hub_msg", "to"),
        ("hub_reply", "to"),
        ("task_snooze", "id"),
        ("hub_spawn", "name"),
        ("hub_capture", "name"),
        ("crystal_search", "query"),
        ("curate", "id"),
        ("curate", "decision"),
        ("hub_ask_ctx", "peer"),
    ],
)
def test_a_tag_without_its_required_attribute_is_not_matched(tag, missing):
    _, attributes, _ = CONVERTED[tag]
    rest = [(name, value) for name, value in attributes.items() if name != missing]

    assert REGISTERED[tag][0].search(_text(tag, rest)) is None


@pytest.mark.parametrize(
    "tag, name, bad",
    [
        ("hub_msg", "to", ""),
        ("hub_reply", "to", ""),
        ("hub_spawn", "name", ""),
        ("hub_capture", "name", ""),
        ("hub_capture", "lines", "many"),
        ("task_snooze", "id", ""),
        ("task_snooze", "minutes", "soon"),
        ("crystal_search", "limit", "some"),
        ("crystal_list", "limit", "some"),
        ("crystal_list", "offset", "some"),
        ("curate", "decision", "drop"),
        ("hub_ask_ctx", "peer", ""),
    ],
)
def test_a_bad_value_leaves_the_tag_unmatched_in_every_order(tag, name, bad):
    _, attributes, _ = CONVERTED[tag]
    pairs = [(n, bad if n == name else v) for n, v in attributes.items()]

    for order in itertools.permutations(pairs):
        assert REGISTERED[tag][0].search(_text(tag, order)) is None, order


def test_an_empty_query_is_still_a_query():
    """crystal_search took query="" before; the handler answers it, not the screen."""
    assert _extract("crystal_search", '<crystal_search query="" />') == {
        "query": "",
        "limit": 5,
    }
    assert _extract("crystal_search", "<crystal_search limit='3' query='' />") == {
        "query": "",
        "limit": 3,
    }


def test_tags_with_no_required_attribute_keep_their_defaults():
    assert _extract("crystal_list", "<crystal_list/>") == {"limit": 20, "offset": 0}
    assert _extract("hub_broadcast", "<hub_broadcast>hi</hub_broadcast>") == {
        "content": "hi",
        "force_attr": "",
    }
    # The handler reports a missing id; the tag is not left on screen for it.
    assert _extract("crystal_delete", "<crystal_delete />") == {
        "entry_id": "",
        "reason": "",
    }
    assert _extract("crystal_edit", "<crystal_edit>text</crystal_edit>") == {
        "entry_id": "",
        "content": "text",
        "summary": None,
        "keywords": None,
    }


def test_crystal_tags_read_an_id_or_an_entry_id():
    for name in ("id", "entry_id"):
        assert _extract("crystal_delete", f'<crystal_delete {name}="c-1"/>') == {
            "entry_id": "c-1",
            "reason": "",
        }
        edit = _extract("crystal_edit", f'<crystal_edit {name}="c-1">b</crystal_edit>')
        assert edit["entry_id"] == "c-1"


def test_hub_msg_thread_and_thread_id_are_one_attribute():
    for name in ("thread", "thread_id"):
        got = _extract("hub_msg", f'<hub_msg {name}="g-1" to="peer">task</hub_msg>')
        assert got["thread_id"] == "g-1"


def test_hub_msg_keeps_an_unclosed_body_and_a_value_with_a_bracket():
    got = _extract("hub_msg", '<hub_msg wait="true" to="lapis">no closing tag')
    assert got["content"] == "no closing tag"
    assert got["wait_attr"] == "true"
    assert _extract("crystal_search", '<crystal_search limit="2" query="a > b"/>') == {
        "query": "a > b",
        "limit": 2,
    }


def test_hub_msg_attribute_names_and_flags_ignore_case():
    got = _extract(
        "hub_msg", '<HUB_MSG WAIT="TRUE" TO="Lapis" KIND="Answer">x</HUB_MSG>'
    )
    assert got["target"] == "Lapis"  # the value is kept as written
    assert got["wait_attr"] == "true"
    assert got["kind"] == "answer"


def test_an_unquoted_value_or_an_unknown_attribute_does_not_leave_the_tag_on_screen():
    got = _extract(
        "hub_msg", "<hub_msg to=lapis urgent priority='high' wait=true>hi</hub_msg>"
    )

    assert (got["target"], got["wait_attr"], got["content"]) == ("lapis", "true", "hi")


def test_a_self_closed_or_sibling_named_tag_is_not_a_hub_msg():
    pattern = REGISTERED["hub_msg"][0]

    assert pattern.search('<hub_msg to="lapis"/>') is None
    assert pattern.search('<hub_msg to="lapis" />trailing prose') is None
    assert pattern.search('<hub_msgs to="lapis">x</hub_msgs>') is None
    assert pattern.search('<hub_msg_extra to="lapis">x</hub_msg_extra>') is None


def test_prose_that_names_the_tag_is_not_swallowed_by_the_real_tag_after_it():
    text = 'A <hub_msg tag looks like: <hub_msg to="x" wait="true">hi</hub_msg>'
    pattern = REGISTERED["hub_msg"][0]

    match = pattern.search(text)

    assert match.group(0) == '<hub_msg to="x" wait="true">hi</hub_msg>'
    assert text[: match.start()] == "A <hub_msg tag looks like: "


@pytest.mark.asyncio
async def test_broadcast_scope_written_in_the_tag_reaches_the_handler():
    """scope="network" is documented for XML; the old pattern never matched it."""
    pattern, extract = REGISTERED["hub_broadcast"]
    hub = HubPlugin(event_bus=MagicMock())
    hub._handle_broadcast_command = AsyncMock(return_value="broadcast sent")

    tool = extract(
        pattern.search(
            '<hub_broadcast scope="network" force="true">all hands</hub_broadcast>'
        )
    )
    result = await hub._handle_hub_broadcast_tool({"id": "b1", **tool})

    assert result.success
    hub._handle_broadcast_command.assert_awaited_once_with(
        "all hands", force=True, scope="network"
    )


def test_two_tags_in_one_reply_are_two_tools_in_their_own_order():
    text = "<hub_msg to=\"a\">one</hub_msg> <hub_msg wait='true' to='b'>two</hub_msg>"
    pattern, extract = REGISTERED["hub_msg"]

    found = [extract(m) for m in pattern.finditer(text)]

    assert [(t["target"], t["content"], t["wait_attr"]) for t in found] == [
        ("a", "one", ""),
        ("b", "two", "true"),
    ]


# --- the helper on its own ----------------------------------------------------


def test_tag_attrs_reads_both_quote_styles_and_bare_values_in_any_order():
    assert tag_attrs(""" b='2' A="1"  c=3 """) == {"a": "1", "b": "2", "c": "3"}


def test_tag_attrs_first_of_a_repeated_attribute_wins():
    assert tag_attrs(' to="first" to="second"') == {"to": "first"}


def test_tag_attrs_does_not_mine_a_quoted_value_for_attributes():
    assert tag_attrs(""" note='to="x" wait="true"' id="1" """) == {
        "note": 'to="x" wait="true"',
        "id": "1",
    }


def test_tag_attrs_needs_a_space_before_a_name():
    assert tag_attrs(' a="1"b="2"') == {"a": "1"}


def test_tag_pattern_builds_a_pattern_the_parser_can_use():
    pattern = tag_pattern("ping", required={"n": r"\d+"}, end="/>")

    assert pattern.search('x <ping n="3"/> y').group(0) == '<ping n="3"/>'
    assert pattern.search('<ping n="x"/>') is None
    assert pattern.sub("", 'x <ping n="3"/> y') == "x  y"


def test_tag_pattern_ends_a_tag_only_at_a_bracket_outside_quotes():
    pattern = tag_pattern("ping", end="/>")

    assert pattern.search("<ping msg=\"a > b\" n='c > d'/>") is not None


@pytest.mark.parametrize(
    "text, attrs, body",
    [
        ('to="lapis"', {"to": "lapis"}, ""),
        ('to="lapis" wait="true"', {"to": "lapis", "wait": "true"}, ""),
        ('wait="true" to="lapis"', {"wait": "true", "to": "lapis"}, ""),
        ("to='lapis' force='TRUE'", {"to": "lapis", "force": "TRUE"}, ""),
        ("to=lapis wait=true", {"to": "lapis", "wait": "true"}, ""),
        ('to="lapis">the message', {"to": "lapis"}, "the message"),
        (
            '<hub_msg wait="true" to="lapis">the message</hub_msg>',
            {"wait": "true", "to": "lapis"},
            "the message",
        ),
    ],
)
def test_embedded_attrs_reads_a_tag_jammed_into_one_argument(text, attrs, body):
    assert embedded_attrs("hub_msg", text) == (attrs, body)


@pytest.mark.parametrize(
    "text",
    [
        "lapis",
        "koordinator@laptop-kollab-m1-mac6",
        "relay:abc123:workspace:agent",
        "",
    ],
)
def test_embedded_attrs_leaves_an_ordinary_target_alone(text):
    assert embedded_attrs("hub_msg", text) is None


# --- through the real parser ----------------------------------------------------


def _parser():
    parser = ResponseParser()
    for name, (pattern, extract) in REGISTERED.items():
        parser.register_plugin_tag(name, pattern, name, extract)
    return parser


def test_a_shuffled_tag_is_run_and_hidden_not_left_on_screen():
    parser = _parser()

    parsed = parser.parse_response(
        'Sending it. <hub_msg wait="true" to="lapis">standing by</hub_msg> Done.'
    )
    tools = parser.get_all_tools(parsed)

    assert [(t["type"], t["target"], t["wait_attr"]) for t in tools] == [
        ("hub_msg", "lapis", "true")
    ]
    assert "hub_msg" not in parsed["content"]
    assert parsed["content"].split() == ["Sending", "it.", "Done."]


def test_a_tag_missing_its_required_attribute_stays_visible_and_unrun():
    parser = _parser()

    parsed = parser.parse_response('<hub_msg wait="true">hello</hub_msg>')

    assert parser.get_all_tools(parsed) == []
    assert parsed["content"] == '<hub_msg wait="true">hello</hub_msg>'
