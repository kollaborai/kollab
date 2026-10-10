"""XML tool calls in the engine's history mirror become web tool cards."""

from kollabor_engine.history_xml_tools import web_history

RAW = "Running it.\n<terminal>echo one</terminal>\n<terminal>ls</terminal>"


def _message(role: str, content: str, metadata: dict | None = None) -> dict:
    return {
        "role": role,
        "content": content,
        "timestamp": "2026-10-08T00:00:00",
        "metadata": dict(metadata or {}),
        "thinking": None,
    }


def _xml_turn(results: list) -> list:
    return [
        _message("user", "runtool"),
        _message(
            "assistant",
            RAW,
            {
                "display_content": "Running it.",
                "xml_tool_calls": [
                    {"id": "terminal_0", "name": "terminal", "input": {"command": "echo one"}},
                    {"id": "terminal_1", "name": "terminal", "input": {"command": "ls"}},
                ],
            },
        ),
        _message(
            "user",
            "\n".join(f"Tool result: {entry['content']}" for entry in results),
            {"tool_output_batch": True, "xml_tool_results": results},
        ),
    ]


def test_each_call_becomes_a_card_with_its_own_result():
    multi_line = "[terminal] a.txt\nb.txt\nc.txt"
    shaped = web_history(
        _xml_turn(
            [
                {"id": "terminal_0", "content": "[terminal] one", "tool_execution_time": 0.01},
                {"id": "terminal_1", "content": multi_line, "is_error": True},
            ]
        )
    )

    assert [message["role"] for message in shaped] == ["user", "assistant", "tool", "tool"]
    assistant = shaped[1]
    assert assistant["content"] == "Running it."
    assert "display_content" not in assistant["metadata"]
    assert "xml_tool_calls" not in assistant["metadata"]
    assert [call["id"] for call in assistant["metadata"]["tool_calls"]] == ["terminal_0", "terminal_1"]
    assert assistant["metadata"]["tool_calls"][1]["function"] == {"name": "terminal", "arguments": {"command": "ls"}}
    first, second = shaped[2], shaped[3]
    assert first["metadata"] == {"tool_call_id": "terminal_0", "tool_execution_time": 0.01}
    assert first["content"] == "[terminal] one"
    # A result that spans lines stays one result on its own card.
    assert second["content"] == multi_line
    assert second["metadata"] == {"tool_call_id": "terminal_1", "is_error": True}


def test_the_daemon_history_is_not_changed():
    history = _xml_turn([{"id": "terminal_0", "content": "[terminal] one"}])
    before = [dict(message, metadata=dict(message["metadata"])) for message in history]

    web_history(history)

    assert history == before
    assert history[1]["content"] == RAW


def test_a_reply_with_only_tags_shows_only_its_cards():
    history = [
        _message(
            "assistant",
            "<terminal>ls</terminal>",
            {"display_content": "", "xml_tool_calls": [{"id": "t", "name": "terminal", "input": {}}]},
        )
    ]

    assert web_history(history)[0]["content"] == ""


def test_native_calls_keep_their_place_before_inline_xml_calls():
    native = {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": "{}"}}
    history = [
        _message(
            "assistant",
            "<hub_msg to='lapis'>hi</hub_msg>",
            {"tool_calls": [native], "xml_tool_calls": [{"id": "hub_msg_0", "name": "hub_msg", "input": {}}]},
        )
    ]

    calls = web_history(history)[0]["metadata"]["tool_calls"]

    assert [call["id"] for call in calls] == ["call_1", "hub_msg_0"]


def test_a_native_turn_shows_without_the_text_call_it_echoed():
    # The daemon did not run the <tool_call> echo of a native call; the reply
    # keeps its native card and shows only its own words.
    native = {"id": "call_1", "type": "function", "function": {"name": "hub_msg", "arguments": "{}"}}
    raw = 'Sent.\n<tool_call>{"to": "lapis"}</tool_call>'
    history = [_message("assistant", raw, {"tool_calls": [native], "display_content": "Sent."})]

    [reply] = web_history(history)

    assert reply["content"] == "Sent."
    assert reply["metadata"] == {"tool_calls": [native]}
    assert history[0]["content"] == raw  # the daemon's own copy is untouched


def test_history_without_xml_turns_is_returned_as_is():
    history = [_message("user", "hi"), _message("assistant", "hello")]

    assert web_history(history) is history


def test_a_batch_written_before_results_were_kept_stays_as_it_was():
    old_batch = _message("user", "Tool result: [terminal] one", {"tool_output_batch": True})
    history = [
        _message("assistant", "Running it.", {"xml_tool_calls": [{"id": "t", "name": "terminal", "input": {}}]}),
        old_batch,
    ]

    assert web_history(history)[1] is old_batch


def test_a_voice_reply_shows_its_display_text_not_its_fields():
    raw = (
        "<display_text>\nhere, koordinator — listening.\n\n[ok] item one\n</display_text>\n"
        "<spoken_text>Yes, I'm here and listening.</spoken_text>"
    )
    history = [_message("user", "hello?"), _message("assistant", raw)]

    shaped = web_history(history)

    assert shaped[1]["content"].strip() == "here, koordinator — listening.\n\n[ok] item one"
    assert shaped[0] is history[0]
    assert history[1]["content"] == raw  # the daemon's own copy is untouched


def test_a_voice_reply_keeps_code_that_shows_the_field_tags():
    raw = (
        "<display_text>Write it like this:\n```\n<spoken_text>hi</spoken_text>\n```"
        "</display_text><spoken_text>Use the spoken field.</spoken_text>"
    )

    [reply] = web_history([_message("assistant", raw)])

    assert "```\n<spoken_text>hi</spoken_text>\n```" in reply["content"]
    assert "Use the spoken field." not in reply["content"]


def test_a_voice_turn_shows_what_the_user_said():
    from kollabor_voice.observer import voice_preamble

    first = {"event_ids": ["a"], "transcript_context": [{"text": "earlier ] line"}]}
    later = {"event_ids": ["b"], "uncertain_event_ids": ["b"]}
    history = [
        _message("user", voice_preamble(first, False) + "Hello, can you hear me?", {"voice": first}),
        _message("user", voice_preamble(later, True) + "Are you there?", {"voice": later}),
        # Saved before the nudge existed: the rules alone, then the words.
        _message("user", "[Voice instructions: Respond concisely.]\nOld turn.", {"voice": {"event_ids": ["c"]}}),
        _message("user", "[voice mode on: the user said this aloud] typed", {}),
    ]

    shown = [m["content"] for m in web_history(history)]

    assert shown == ["Hello, can you hear me?", "Are you there?", "Old turn.", history[3]["content"]]
    assert history[0]["content"].startswith("[Voice instructions: ")  # the daemon's copy


def test_a_silent_reply_to_speech_shows_nothing():
    heard = "[voice mode on: the user said this aloud]\n"
    voice = {"voice": {"event_ids": ["a"]}}
    history = [
        _message("user", heard + "Hmm.", voice),
        _message("assistant", "."),
        _message("user", "typed"),
        _message("assistant", "."),  # a typed turn's period is an answer
        _message("user", heard + "Run it.", voice),
        _message("assistant", "", {"tool_calls": [{"id": "t", "name": "terminal"}]}),
        {"role": "tool", "content": "ok", "metadata": {"tool_call_id": "t"}},
        _message("assistant", "."),  # still answering the speech, after its tool
    ]

    shaped = web_history(history)

    assert [m["content"] for m in shaped] == ["Hmm.", "", "typed", ".", "Run it.", "", "ok", ""]
    assert len(shaped) == len(history)  # rows stay, so the web's message ids line up
