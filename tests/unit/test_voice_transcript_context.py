"""Prior microphone lines inform decisions without becoming fresh user requests."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor_voice.classifiers import (
    DecisionRequest,
    LayaClassifier,
    VoiceDecision,
    load_context_lines,
    save_classifier,
    save_context_lines,
)
from kollabor_voice.control import VoiceError
from kollabor_voice.observer import parse_decision, voice_instructions
from kollabor_voice.store import utc

from kollabor.commands.system_commands.handlers.voicemode import VoiceModeCommandHandler
from plugins.voice_plugin import VoicePlugin


def record(number, text, epoch="owner"):
    return {
        "event_id": str(number),
        "seq": number,
        "text": text,
        "owner_epoch": epoch,
        "stream_id": "stream",
        "started_at": utc(time.time() - 25 + number * 5),
        "ended_at": utc(time.time() - 24 + number * 5),
    }


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    value = VoicePlugin(
        event_bus=SimpleNamespace(get_service=lambda _: None), config={}
    )
    value._lease = {"epoch": "owner", "token": "token", "stream_id": "stream"}
    value._client = SimpleNamespace(call=AsyncMock())
    return value


@pytest.mark.asyncio
async def test_ignored_lines_reach_later_decisions_and_main_agent_without_replay(
    plugin,
):
    tv = record(1, "I'm watching TV. That is background noise.")
    hello = record(2, "Hello.")
    request = record(3, "Kollab, check the tests.")
    observed, delivered = [], []
    plugin.classifier_name = "provider"
    plugin.requested = True
    plugin._pending = [tv]

    async def observe(records, transcript_context):
        observed.append((records, list(transcript_context)))
        return records if records == [request] else []

    async def deliver(data):
        delivered.append(data)
        plugin.requested = False
        return {"status": "queued"}

    plugin._observe_with_provider = observe
    plugin._deliver = deliver
    task = asyncio.create_task(plugin._observe_loop())
    try:
        async with asyncio.timeout(2):
            for following in (hello, request):
                while plugin._pending:
                    await asyncio.sleep(0.01)
                plugin._pending.append(following)
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert observed == [([tv], []), ([hello], [tv]), ([request], [tv, hello])]
    assert delivered[0]["message"] == request["text"]
    assert delivered[0]["voice"]["event_ids"] == [request["event_id"]]
    assert delivered[0]["voice"]["transcript_context"] == [tv, hello]
    assert tv["text"] in voice_instructions(delivered[0]["voice"])
    assert plugin._recent_transcripts == [tv, hello, request]


def test_context_window_excludes_candidates_future_and_other_capture(plugin):
    past = [record(i, f"Line {i}.") for i in range(1, 14)]
    current = record(14, "Hello.")
    plugin._recent_transcripts = [
        *past,
        current,
        record(15, "Future line."),
        record(13, "Another owner.", epoch="different"),
        {**record(13, "Another stream."), "stream_id": "different"},
    ]
    assert plugin._transcript_context_for([current]) == past[-10:]
    plugin.set_context_lines(3)
    assert plugin._transcript_context_for([current]) == past[-3:]
    assert load_context_lines() == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["laya", "provider"])
async def test_same_batch_ignored_notice_reaches_main_agent_as_history(
    plugin, provider
):
    older = record(1, "The television is on.")
    tv = record(2, "That is background noise.")
    hello = record(3, "Hello, can you hear me?")
    later = record(4, "The program continues.")
    delivered = []
    plugin.classifier_name = provider
    plugin._state["classifier"] = {"state": "ready"}
    plugin.requested = True
    plugin._recent_transcripts = [older]
    plugin._pending = [tv, hello, later]

    async def call(op, **params):
        if op == "classifier_submit":
            assert params["transcript_context"] == [older]
            return {"job_id": "job"}
        if op == "classifier_result":
            return {
                "done": True,
                "result": VoiceDecision(
                    "defer",
                    ["3"],
                    "laya",
                    deferred_event_ids=["3"],
                    context_event_ids=["1"],
                ).to_wire(),
            }
        return {}

    async def deliver(data):
        delivered.append(data)
        plugin.requested = False
        return {"status": "queued"}

    plugin._client.call = call
    plugin._observe_with_provider = AsyncMock(return_value=[hello])
    plugin._deliver = deliver
    async with asyncio.timeout(2):
        await plugin._observe_loop()
    assert delivered[0]["message"] == hello["text"]
    assert delivered[0]["voice"]["event_ids"] == ["3"]
    assert delivered[0]["voice"]["transcript_context"] == [older, tv]
    assert later["text"] not in voice_instructions(delivered[0]["voice"])
    if provider == "laya":
        assert delivered[0]["voice"]["uncertain_event_ids"] == ["3"]


@pytest.mark.asyncio
async def test_stop_clears_transcript_context_but_provider_change_preserves_it(plugin):
    plugin._recent_transcripts = [record(1, "The TV is on.")]
    await plugin.set_classifier("provider")
    assert plugin._recent_transcripts
    await plugin.stop_voice()
    assert not plugin._recent_transcripts


@pytest.mark.asyncio
async def test_attached_provider_receives_context_and_acknowledges_it(plugin):
    history = [record(1, "The TV is background noise.")]
    current = [record(2, "Hello.")]
    remote = VoicePlugin(
        event_bus=SimpleNamespace(get_service=lambda _: None), config={}
    )
    await remote._remote_bind({"epoch": "owner", "client_id": "client"})
    remote._observe_with_provider = AsyncMock(return_value=[])
    plugin._event_bus = SimpleNamespace(get_service=lambda _: object())

    async def job(rpc, method, params):
        assert method == "voice.observe"
        return await remote._do_remote_observe(params)

    plugin._remote_job = job
    assert await plugin._observe_with_provider(current, history) == []
    remote._observe_with_provider.assert_awaited_once_with(
        current, transcript_context=history
    )
    plugin._remote_job = AsyncMock(return_value={"event_ids": []})
    with pytest.raises(RuntimeError, match="acknowledge transcript context"):
        await plugin._observe_with_provider(current, history)


@pytest.mark.asyncio
@pytest.mark.parametrize("updated_worker", [True, False])
async def test_laya_transport_carries_history_and_rejects_old_worker(
    plugin, updated_worker
):
    history = [record(1, "The TV is background noise.")]
    current = [record(2, "Hello.")]
    decision = VoiceDecision(
        "ignore", [], "laya", context_event_ids=["1"] if updated_worker else []
    )
    plugin._client.call = AsyncMock(
        side_effect=[{"job_id": "job"}, {"done": True, "result": decision.to_wire()}]
    )
    call = LayaClassifier(plugin._client, plugin._auth).decide(
        DecisionRequest(current, "", history, 3)
    )
    if updated_worker:
        assert (await call).decision == "ignore"
    else:
        with pytest.raises(VoiceError, match="acknowledge transcript context"):
            await call
    params = plugin._client.call.call_args_list[0].kwargs
    assert params["records"] == current and params["transcript_context"] == history
    assert params["context_lines"] == 3


@pytest.mark.parametrize("oversized", [True, False])
def test_worker_receives_history_intact_or_defers_without_truncation(oversized):
    from kollabor_voice.laya_worker import predict

    states = []

    def classify(state, _):
        states.append(state)
        return {
            "answers": {
                "response": {
                    "choice": "ignore",
                    "probabilities": {"ignore": 0.99, "respond": 0.01},
                }
            }
        }

    agent = SimpleNamespace(
        cfg={"max_len": 1024, "head_max_len": 256},
        tok=SimpleNamespace(encode=lambda _: [0] * (1000 if oversized else 100)),
        predict=classify,
    )
    history = [record(1, "The TV is background noise.")]
    current = [record(2, "Hello.")]
    decision = predict(agent, current, "", history)
    assert decision.context_event_ids == ["1"]
    if oversized:
        assert decision.decision == "defer" and not states
        assert decision.selected(current) == current
    else:
        assert decision.decision == "ignore"
        assert states[0]["text"] == "Hello."
        assert states[0]["recent_transcripts"] == [history[0]["text"]]


def test_history_ids_are_not_valid_new_admissions():
    with pytest.raises(VoiceError):
        parse_decision(
            '{"decision":"respond","event_ids":["old"]}', [record(2, "Hello.")]
        )


@pytest.mark.parametrize("with_history", ["ignore", "respond"])
def test_context_disagreement_defers_full_request_instead_of_dropping_or_admitting(
    with_history,
):
    from kollabor_voice.laya_worker import predict

    def classify(state, _):
        choice = (
            with_history
            if "recent_transcripts" in state
            else ("respond" if with_history == "ignore" else "ignore")
        )
        return {
            "answers": {
                "response": {
                    "choice": choice,
                    "probabilities": {
                        choice: 0.99,
                        "respond" if choice == "ignore" else "ignore": 0.01,
                    },
                }
            }
        }

    agent = SimpleNamespace(
        cfg={"max_len": 1024, "head_max_len": 256},
        tok=SimpleNamespace(encode=lambda _: [0]),
        predict=classify,
    )
    history = [record(1, "The TV is background noise.")]
    current = [record(2, "Kollab, please check the tests.")]
    decision = predict(agent, current, "", history)
    assert decision.decision == "defer"
    assert decision.deferred_event_ids == ["2"]
    assert decision.context_event_ids == ["1"]
    assert decision.confidence is None


def _laya_agent(with_history, alone):
    """Fake Laya head: (choice, probability) for the pass with history, and without."""

    def classify(state, _):
        has_history = bool(state.get("recent_transcripts") or state["recent_conversation"])
        choice, p = with_history if has_history else alone
        other = "respond" if choice == "ignore" else "ignore"
        return {
            "answers": {
                "response": {"choice": choice, "probabilities": {choice: p, other: 1 - p}}
            }
        }

    return SimpleNamespace(
        cfg={"max_len": 1024, "head_max_len": 256},
        tok=SimpleNamespace(encode=lambda _: [0]),
        predict=classify,
    )


@pytest.mark.parametrize(
    "with_history, alone, expected",
    [
        # history drags clear speech under 0.8: the pass that is sure decides
        (("ignore", 0.71), ("respond", 0.99), "respond"),  # "Hello, can you hear me?"
        (("respond", 0.71), ("respond", 0.99), "respond"),
        (("respond", 0.607), ("ignore", 0.998), "ignore"),  # background chatter
        (("ignore", 0.607), ("ignore", 0.998), "ignore"),
        (("respond", 0.99), ("ignore", 0.6), "respond"),  # history is the sure one
        # no pass is sure, or the sure ones split: the agent decides
        (("ignore", 0.71), ("respond", 0.6), "defer"),
        (("ignore", 0.99), ("respond", 0.99), "defer"),
    ],
)
def test_sure_pass_decides_so_history_cannot_demote_clear_speech(
    with_history, alone, expected
):
    from kollabor_voice.laya_worker import predict

    history = [record(1, "The TV is background noise.")]
    current = [record(2, "Hello, can you hear me?")]
    decision = predict(_laya_agent(with_history, alone), current, "", history)
    assert decision.decision == expected
    assert decision.deferred_event_ids == (["2"] if expected == "defer" else [])
    if expected == "respond" and with_history[1] < 0.8:
        assert decision.confidence == alone[1]


def test_conversation_context_alone_adds_a_second_pass_only_when_unsure():
    from kollabor_voice.laya_worker import predict

    current = [record(2, "Hello, can you hear me?")]
    rescued = _laya_agent(("ignore", 0.71), ("respond", 0.99))
    assert predict(rescued, current, "assistant: Done.", []).decision == "respond"
    sure = _laya_agent(("respond", 0.95), ("ignore", 0.99))
    assert predict(sure, current, "assistant: Done.", []).decision == "respond"


def _heard(text, *spoken):
    return {
        "text": text,
        "playback_overlap": [{"reply_id": "r", "text": s} for s in spoken],
    }


@pytest.mark.parametrize(
    "text, spoken",
    [
        ("Yes, I can hear you.", ["Yes, I can hear you."]),
        ("yes i can hear you", ["Yes, I can hear you."]),
        ("I can hear you", ["Yes, I can hear you."]),  # a chunk of the reply
        ("Yes I can here you", ["Yes, I can hear you."]),  # one transcription slip
        ("Yes, I can hear you. Anything else?", ["Yes, I can hear you.", "Anything else?"]),
        ("Okay.", ["Okay."]),
        ("I'll handle it here.", ["I’ll handle it here."]),  # curly apostrophe
    ],
)
def test_own_playback_picked_up_by_the_mic_is_echo(text, spoken):
    from kollabor_voice.observer import is_playback_echo

    assert is_playback_echo(_heard(text, *spoken))


@pytest.mark.parametrize(
    "text, spoken",
    [
        ("Hello, can you hear me?", ["Yes, I can hear you."]),  # the human, same room
        ("Stop.", ["I will stop the music now."]),  # barge-in on a word the reply says
        ("Run the unit tests please", ["Do you want me to check the build?"]),
        ("Hello, can you hear me?", []),  # nothing was playing
    ],
)
def test_the_humans_words_over_playback_are_not_echo(text, spoken):
    from kollabor_voice.observer import is_playback_echo

    assert not is_playback_echo(_heard(text, *spoken))


@pytest.mark.asyncio
async def test_context_command_defaults_validation_persistence_and_no_activation(
    plugin, tmp_path
):
    handler = VoiceModeCommandHandler(
        None, SimpleNamespace(get_service=lambda _: plugin)
    )
    assert (
        "10"
        in (await handler.handle_voicemode(SimpleNamespace(args=["context"]))).message
    )
    save_classifier("provider")
    for argument in ("0", "51", "invalid"):
        result = await handler.handle_voicemode(
            SimpleNamespace(args=["context", argument])
        )
        assert not result.success
    result = await handler.handle_voicemode(SimpleNamespace(args=["context", "20"]))
    assert result.success and not plugin.requested
    assert load_context_lines() == 20
    assert (
        json.loads((tmp_path / "preferences.json").read_text())["classifier"]
        == "provider"
    )
    for invalid in (True, None, 0, 51):
        with pytest.raises(ValueError):
            save_context_lines(invalid)


def test_decision_traces_what_laya_read_and_each_pass():
    """`/voicemode classifier` shows this: the state read and both passes' answers."""
    from kollabor_voice.classifiers import VoiceDecision
    from kollabor_voice.laya_worker import predict

    history = [record(1, "The TV is background noise.")]
    current = [record(2, "Hello, can you hear me?")]
    decision = predict(
        _laya_agent(("ignore", 0.71), ("respond", 0.99)), current, "", history
    )

    [trace] = VoiceDecision.from_wire(decision.to_wire()).trace
    assert trace["state"]["text"] == "Hello, can you hear me?"
    assert trace["state"]["recent_transcripts"] == ["The TV is background noise."]
    assert trace["passes"] == [
        {"label": "with context", "choice": "ignore", "probability": 0.71},
        {"label": "words alone", "choice": "respond", "probability": 0.99},
    ]
