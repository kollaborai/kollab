"""The voice classifier's own lines: acknowledgements, answers, questions, progress."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor_voice.acks import LINES, address, answer, kind_of, pick
from kollabor_voice.store import utc

import plugins.voice_plugin as voice_plugin
from plugins.voice_plugin import VoicePlugin

NAMES = ["Kollab", "Aquamarine"]


@pytest.mark.parametrize(
    "text, kind",
    [
        ("Hello, can you hear me?", "mic_check"),
        ("Testing, one two three.", "mic_check"),
        ("Thank you so much.", "thanks"),
        ("Aquamarine.", "greeting"),
        ("Hey Aquamarine, what are you working on?", "question"),
        ("Tell me what changed today", "question"),
        ("Can you run the tests?", "task"),
        ("Please fix the login bug", "task"),
        ("I think the build is broken", "request"),
    ],
)
def test_kind_of_picks_the_line_the_words_call_for(text, kind):
    assert kind_of(text, NAMES) == kind


def test_a_mic_check_or_thanks_never_swallows_a_request():
    assert kind_of("Can you hear me? Run the tests.", NAMES) != "mic_check"
    assert kind_of("Thanks, now deploy it to staging please", NAMES) != "thanks"


def test_address_counts_only_the_vocative():
    assert address("Aquamarine, run the tests.", NAMES) == (
        True,
        ["run", "the", "tests"],
    )
    assert address("Run the tests, Aquamarine?", NAMES) == (
        True,
        ["run", "the", "tests"],
    )
    # Whisper splits the name; a greeting may come first.
    assert address("Hey aqua marine, what's up", NAMES) == (True, ["what's", "up"])
    assert not address("I told Aquamarine to run the tests.", NAMES)[0]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Yes.", "yes"),
        ("Yeah, that was for you.", "yes"),
        ("I was talking to you", "yes"),
        ("No.", "no"),
        ("Nope, it's the TV.", "no"),
        ("Not you, sorry.", "no"),
        ("It's a movie", "no"),
        ("That was my wife, you can ignore.", "no"),
        ("You can ignore that.", "no"),
        ("It was my son.", "no"),
        ("Don't ignore it, that was for you.", "yes"),
        ("It was.", "yes"),
        ("You.", None),
        ("What time is it?", None),
    ],
)
def test_answer_reads_the_reply_to_was_that_for_me(text, expected):
    assert answer(text) == expected


def test_pick_never_repeats_the_previous_line():
    for kind, lines in LINES.items():
        assert pick(kind) in lines
        assert all(pick(kind, lines[0]) != lines[0] for _ in range(20))


def record(number, text):
    end = time.time()
    return {
        "event_id": str(number),
        "seq": number,
        "text": text,
        "owner_epoch": "owner",
        "stream_id": "stream",
        "started_at": utc(end - 2),
        "ended_at": utc(end),
    }


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    value = VoicePlugin(
        event_bus=SimpleNamespace(get_service=lambda _: None), config={}
    )
    value._lease = {"epoch": "owner", "token": "token", "stream_id": "stream"}
    value._client = SimpleNamespace(call=AsyncMock(return_value={}))
    value._agent_names = NAMES
    value.classifier_name = "provider"
    value.requested = True
    value.delivered = []

    async def deliver(data):
        value.delivered.append(data)
        return {"status": "queued"}

    value._deliver = deliver
    return value


def spoken(plugin):
    calls = plugin._client.call.call_args_list
    return [c.kwargs["text"] for c in calls if c.args[:1] == ("voice_out",)]


async def hear(plugin, *records, until=lambda: True):
    """Run the observer over these microphone lines until the condition holds."""
    loop = asyncio.create_task(plugin._observe_loop())
    try:
        async with asyncio.timeout(2):
            for line in records:
                plugin._pending.append(line)
                while plugin._pending:
                    await asyncio.sleep(0.01)
            while not until():
                await asyncio.sleep(0.01)
    finally:
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)
    assert plugin._observer_error is None


def unsure(plugin, *event_ids):
    """The classifier is unsure about these lines and ignores the rest."""

    async def observe(records):
        chosen = [r for r in records if r["event_id"] in event_ids]
        plugin._decision_deferred_ids = [r["event_id"] for r in chosen]
        return chosen

    plugin.observe_records = observe


@pytest.mark.asyncio
async def test_called_by_name_goes_to_the_agent_after_an_ack(plugin):
    plugin.observe_records = AsyncMock(return_value=[])
    await hear(
        plugin, record(1, "Aquamarine, run the tests."), until=lambda: plugin.delivered
    )
    [data] = plugin.delivered
    assert data["message"] == "Aquamarine, run the tests."
    assert spoken(plugin) == [data["voice"]["spoken_ack"]]
    assert not data["voice"]["answered"]
    assert data["voice"]["spoken_ack"] in LINES["task"]
    assert plugin.observe_records.await_count == 0


@pytest.mark.asyncio
async def test_a_mic_check_is_answered_at_once_and_still_reaches_the_agent(plugin):
    plugin.observe_records = AsyncMock(side_effect=lambda records: records)
    await hear(
        plugin, record(1, "Hello, can you hear me?"), until=lambda: plugin.delivered
    )
    [data] = plugin.delivered
    assert spoken(plugin) == [data["voice"]["spoken_ack"]]
    assert data["voice"]["spoken_ack"] in LINES["mic_check"]
    assert data["voice"]["answered"]  # nobody waits: no still-working lines


@pytest.mark.asyncio
async def test_unsure_speech_is_asked_about_and_a_yes_sends_it(plugin):
    unsure(plugin, "1")
    held = record(1, "Check the build status.")
    await hear(plugin, held, until=lambda: plugin._clarifying)
    assert spoken(plugin)[0] in LINES["clarify"]
    assert plugin._activity({"state": "listening"})["stage"] == "asking"
    await hear(plugin, record(2, "Yes."), until=lambda: plugin.delivered)
    [data] = plugin.delivered
    assert data["message"] == held["text"]
    assert data["voice"]["spoken_ack"] in LINES["confirmed"]
    assert plugin._clarifying is None


@pytest.mark.asyncio
async def test_no_it_was_the_tv_stays_silent_and_stops_asking(plugin):
    unsure(plugin, "1", "3")
    await hear(
        plugin, record(1, "Check the build status."), until=lambda: plugin._clarifying
    )
    await hear(plugin, record(2, "No, it's the TV."))
    assert plugin._clarifying is None
    await hear(plugin, record(3, "The weather turns cold tonight."))
    assert len(spoken(plugin)) == 1  # only the first question
    assert plugin.delivered == []


@pytest.mark.asyncio
async def test_background_talk_never_answers_the_question(plugin):
    unsure(plugin, "1")
    await hear(
        plugin, record(1, "Check the build status."), until=lambda: plugin._clarifying
    )
    await hear(plugin, record(2, "And now the weather for the weekend."))
    assert plugin.delivered == [] and plugin._clarifying
    # Nobody answered in time: the question lapses and asking pauses a while.
    plugin._clarifying["asked"] -= voice_plugin.CLARIFY_WINDOW_S + 1
    await hear(plugin, record(3, "Yes."))
    assert plugin.delivered == [] and plugin._clarifying is None
    assert plugin._clarify_pause_until > time.monotonic()


@pytest.mark.asyncio
async def test_a_bare_name_greets_and_the_next_words_go_to_the_agent(plugin):
    plugin.observe_records = AsyncMock(return_value=[])  # would drop them
    await hear(plugin, record(1, "Aquamarine."), until=lambda: plugin.delivered)
    assert spoken(plugin)[0] in LINES["greeting"]
    assert plugin.delivered[0]["voice"]["spoken_ack"] == spoken(plugin)[0]
    await hear(
        plugin,
        record(2, "What's the build status?"),
        until=lambda: len(plugin.delivered) == 2,
    )
    assert plugin.delivered[1]["message"] == "What's the build status?"
    assert plugin.delivered[1]["voice"]["spoken_ack"] in LINES["question"]
    assert plugin.observe_records.await_count == 0


@pytest.mark.asyncio
async def test_still_working_lines_stop_once_the_agent_answers(plugin, monkeypatch):
    monkeypatch.setattr(voice_plugin, "PROGRESS_AFTER_S", (0.01, 0.01))
    llm = SimpleNamespace(is_processing=True)
    plugin._event_bus = SimpleNamespace(
        get_service=lambda name: llm if name == "llm_service" else None
    )
    said = []

    async def voice_out(text, reply_id, epoch, producer):
        said.append((text, reply_id, producer))

    plugin.voice_out = voice_out
    voice = {"reply_id": "r", "epoch": "owner"}
    plugin._watch_progress(voice)
    await plugin._progress_task
    assert [s[2] for s in said] == ["voice-progress", "voice-progress"]
    assert all(s[0] in LINES["still_working"] for s in said)
    assert said[0][0] != said[1][0]
    said.clear()
    plugin._spoken_replies.append("r")
    plugin._watch_progress(voice)
    await plugin._progress_task
    assert said == []


def test_activity_shows_what_voice_is_doing(plugin):
    state = {"state": "listening", "level": 10 ** (-30 / 20)}  # -30 dBFS
    assert plugin._activity(state)["stage"] == "listening"
    assert plugin._activity(state)["level"] == pytest.approx(0.67, abs=0.01)
    assert plugin._activity({**state, "speech_active": True})["stage"] == "hearing"
    playing = {**state, "output": {"state": "playing"}}
    assert plugin._activity(playing)["stage"] == "speaking"
    assert plugin._activity({"state": "starting"})["stage"] == "starting"
    plugin._clarifying = {"records": [], "asked": time.monotonic()}
    assert plugin._activity(state)["stage"] == "asking"
    plugin._clarifying["asked"] -= voice_plugin.CLARIFY_WINDOW_S + 1
    assert plugin._activity(state)["stage"] == "listening"
    plugin._last_said = {"text": "One moment.", "at": time.time()}
    assert plugin._activity(state)["said"] == "One moment."
    plugin._last_said["at"] -= 11
    assert plugin._activity(state)["said"] is None


# Self-recovery: nothing here waits for /voice retry.


@pytest.mark.asyncio
async def test_a_failed_delivery_retries_on_its_own_and_acks_once(plugin):
    plugin._observer_backoff = 0.05
    plugin.observe_records = AsyncMock(side_effect=lambda records: records)
    attempts = []

    async def deliver(data):
        attempts.append(data)
        if len(attempts) == 1:
            return {"status": "busy"}
        plugin.delivered.append(data)
        return {"status": "queued"}

    plugin._deliver = deliver
    await hear(
        plugin, record(1, "Can you check the build?"), until=lambda: plugin.delivered
    )
    assert len(attempts) == 2
    assert len(spoken(plugin)) == 1  # the ack is said once
    assert plugin._observer_backoff == voice_plugin.OBSERVER_RETRY_S


@pytest.mark.asyncio
async def test_speech_that_went_stale_becomes_context_not_a_stop(plugin):
    old = record(1, "Check the build status.")
    old["started_at"], old["ended_at"] = utc(time.time() - 47), utc(time.time() - 45)
    plugin.observe_records = AsyncMock(return_value=[])
    await hear(plugin, old)
    assert plugin.delivered == [] and spoken(plugin) == []
    assert plugin._recent_transcripts == [old]
    assert plugin.observe_records.await_count == 0


@pytest.mark.asyncio
async def test_the_question_waits_until_voice_stops_talking(plugin):
    unsure(plugin, "1")
    held = record(1, "Check the build status.")
    await hear(plugin, held, until=lambda: plugin._clarifying)
    # The agent's reply played after the question, past the window from asking.
    plugin._clarifying["asked"] -= voice_plugin.CLARIFY_WINDOW_S + 5
    plugin._spoke_at = time.monotonic()
    assert plugin._activity({"state": "listening"})["stage"] == "asking"
    await hear(plugin, record(2, "Yes."), until=lambda: plugin.delivered)
    assert plugin.delivered[0]["message"] == held["text"]


def qwen_service(plugin, answer):
    """A voice service whose Qwen worker answers "was that for me?" with ``answer``."""
    asked = []

    async def call(op, **params):
        if op == "classifier_submit":
            asked.append(params)
            return {"job_id": "job"}
        if op == "classifier_result":
            return {
                "done": True,
                "result": {
                    "answer": answer,
                    "probabilities": {
                        "yes": 0.03,
                        "no": 0.02,
                        "other": 0.01,
                        answer: 0.94,
                    },
                    "provider": "qwen",
                    "model": "qwen3-0.6b-voice-v1",
                },
            }
        return {}

    plugin.classifier_name = "qwen"
    plugin._state = {"classifier": {"state": "ready", "name": "qwen"}}
    plugin._client.call = AsyncMock(side_effect=call)
    return asked


@pytest.mark.asyncio
async def test_qwen_reads_the_answer_with_the_held_words_and_the_question(plugin):
    asked = qwen_service(plugin, "no")
    unsure(plugin, "1")
    held = record(1, "Check the build status.")
    await hear(plugin, held, until=lambda: plugin._clarifying)
    question = spoken(plugin)[0]
    assert question in LINES["clarify"] and plugin._clarifying["question"] == question
    # The rules would call this a yes; the model's no decides.
    await hear(plugin, record(2, "Yeah right, that was the TV."))
    assert asked[-1]["task"] == "answer" and asked[-1]["model"] == "qwen"
    assert (asked[-1]["held"], asked[-1]["question"]) == (held["text"], question)
    assert asked[-1]["text"] == "Yeah right, that was the TV."
    assert plugin.delivered == [] and plugin._clarifying is None
    entry = plugin.classifier_log()[-1]
    assert entry["decision"]["decision"] == "ignore"
    assert entry["decision"]["model"] == "qwen3-0.6b-voice-v1"
    assert entry["decision"]["confidence"] == 0.94
    # /voice classifier shows what the model read, not the transcript window.
    (trace,) = entry["decision"]["trace"]
    assert trace["state"] == {
        "held": held["text"],
        "question": question,
        "reply": "Yeah right, that was the TV.",
    }
    assert trace["passes"] == [{"label": "answer", "choice": "no", "probability": 0.94}]


@pytest.mark.asyncio
async def test_qwen_yes_sends_the_held_words_and_other_keeps_listening(plugin):
    qwen_service(plugin, "other")
    unsure(plugin, "1")
    held = record(1, "Check the build status.")
    await hear(plugin, held, until=lambda: plugin._clarifying)
    await hear(plugin, record(2, "And now the weather."))
    assert plugin.delivered == [] and plugin._clarifying  # not an answer: still asking
    qwen_service(plugin, "yes")
    await hear(plugin, record(3, "Mm-hmm."), until=lambda: plugin.delivered)
    [data] = plugin.delivered
    assert data["message"] == held["text"]


def test_the_chat_waits_for_its_own_classifier_and_names_an_old_service(plugin):
    plugin.classifier_name = "qwen"
    ready = {"state": "ready", "name": "qwen"}
    assert plugin._classifier_status({"classifier": ready}) == ready
    # The service has Laya loaded: Qwen is not ready, so the chat prepares it.
    assert plugin._classifier_status(
        {"classifier": {"state": "ready", "name": "laya"}}
    ) == {"state": "off"}
    old = plugin._classifier_status({"classifier": {"state": "ready"}})
    assert old["state"] == "error" and "restart the voice service" in old["detail"]
    plugin.classifier_name = "laya"
    assert (
        plugin._classifier_status({"classifier": {"state": "ready"}})["state"]
        == "ready"
    )


@pytest.mark.asyncio
async def test_prepare_names_the_model_and_a_switch_prepares_again(plugin):
    plugin.classifier_name = "qwen"
    plugin._client.call = AsyncMock(
        return_value={"state": "installing", "name": "qwen"}
    )
    await plugin._prepare_classifier()
    plugin._client.call.assert_awaited_with("classifier_prepare", model="qwen")
    assert plugin._state["classifier"]["name"] == "qwen"
    assert plugin._local_classifier
    plugin.classifier_name = "provider"
    assert not plugin._local_classifier
