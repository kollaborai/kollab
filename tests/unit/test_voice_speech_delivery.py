import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor_voice.client import VoiceClient
from kollabor_voice.speech_review import SpeechDecision

from plugins.voice_plugin import VoicePlugin


class Bus:
    def get_service(self, name):
        return None


def plugin():
    value = VoicePlugin(event_bus=Bus(), config={})
    value._lease = {"epoch": "owner", "token": "capability"}
    value.validate_turn = AsyncMock()
    value.classifier_name = "laya"
    value._client = VoiceClient()
    value._client.review_speech = AsyncMock(
        return_value=SpeechDecision("speak", provider="laya")
    )
    value._client.call = AsyncMock(return_value={"ids": ["sentence"]})
    value._rewrite_speech = AsyncMock(return_value="The short answer is ready.")
    return value


def reply(spoken, display="Visible code: ```python\nprivate_example()\n```", **extra):
    return {
        "voice": {"epoch": "owner", "reply_id": "answer"},
        "display_text": display,
        "spoken_text": spoken,
        **extra,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["rewrite", "review"])
async def test_attached_speech_does_not_block_its_own_rpc_reply(operation):
    from kollabor_rpc import RpcClient

    incoming = asyncio.Queue()
    methods, spoken = [], []
    played = asyncio.Event()

    class Writer:
        def write(self, raw):
            request = json.loads(raw)
            methods.append(request["method"])
            result = {"job_id": "job"}
            if request["method"] == "voice.result":
                result = {
                    "done": True,
                    "result": (
                        {"spoken_text": "The short answer is ready."}
                        if operation == "rewrite"
                        else SpeechDecision("speak", provider="provider").to_wire()
                    ),
                }
            incoming.put_nowait(
                {
                    "action": "rpc_reply",
                    "request_id": request["request_id"],
                    "result": result,
                }
            )

        async def drain(self):
            pass

    value = plugin()
    value.requested = True
    del value._rewrite_speech
    rpc = RpcClient(Writer())
    original_call = rpc.call

    async def call(method, params=None, **kwargs):
        return await original_call(method, params, timeout=0.1)

    rpc.call = call
    value._event_bus = SimpleNamespace(
        get_service=lambda n: rpc if n == "rpc_client" else None
    )
    if operation == "review":
        value._client.review_speech.return_value = SpeechDecision(
            "defer", provider="laya"
        )

    async def device_call(op, **params):
        if op == "voice_out":
            spoken.append(params["text"])
            played.set()
        return {"ids": ["sentence"]}

    value._client.call = device_call

    async def read_events():
        while True:
            event = await incoming.get()
            if event.get("action") == "rpc_reply":
                rpc.on_reply(event)
            else:
                # The attached UI awaits this handler on its only socket reader.
                await value.handle_remote_reply(event)

    reader = asyncio.create_task(read_events())
    text = (
        "One sentence. Two sentences. Three sentences."
        if operation == "rewrite"
        else "The tests passed."
    )
    incoming.put_nowait(
        {"spoken_text": text, "reply_id": "reply", "owner_epoch": "owner"}
    )
    try:
        async with asyncio.timeout(0.8):
            await played.wait()
        assert value._speech_error is None
        assert f"voice.{operation}_speech" in methods
        assert spoken == [
            "The short answer is ready." if operation == "rewrite" else text
        ]
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        rpc.close()
        await value.stop_voice()


@pytest.mark.asyncio
async def test_attached_transport_carries_only_explicit_narration_and_rewrite_budget(
    monkeypatch,
):
    import kollabor_tui.display_tap as tap

    events = []
    monkeypatch.setattr(
        tap, "publish_semantic", lambda bus, kind, **data: events.append((kind, data))
    )
    value = plugin()
    value._remote_binding = {"epoch": "owner"}
    await value._spoken_reply(reply("The code is on screen.", rewrite_count=1))
    assert events == [
        (
            "voice_reply",
            {
                "spoken_text": "The code is on screen.",
                "reply_id": "answer",
                "owner_epoch": "owner",
                "producer_id": value.client_id,
                "rewrite_count": 1,
            },
        )
    ]
    value._client.call.assert_not_awaited()
    device = plugin()
    device.requested = True
    device._spoken_reply = AsyncMock()
    await device.handle_remote_reply(events[0][1])
    await device._remote_reply_queue.join()
    data = device._spoken_reply.call_args.args[0]
    assert data["spoken_text"] == "The code is on screen."
    assert data["rewrite_count"] == 1 and "display_text" not in data


@pytest.mark.asyncio
async def test_remote_replies_keep_order_without_holding_event_reader():
    value = plugin()
    value.requested = True
    started, release = asyncio.Event(), asyncio.Event()
    spoken = []

    async def speak(data):
        if data["voice"]["reply_id"] == "first":
            started.set()
            await release.wait()
        spoken.append(data["spoken_text"])

    value._spoken_reply = speak
    try:
        for name in ["first", "second", "third"]:
            async with asyncio.timeout(0.1):
                await value.handle_remote_reply(
                    {
                        "owner_epoch": "owner",
                        "reply_id": name,
                        "spoken_text": name,
                    }
                )
        await started.wait()
        assert not spoken
        assert value.status()["pending_replies"] == 2
        release.set()
        await value._remote_reply_queue.join()
        assert spoken == ["first", "second", "third"]
    finally:
        await value.stop_voice()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "stop", "shutdown"])
async def test_cancel_or_stop_clears_current_and_queued_remote_speech(action):
    value = plugin()
    value.requested = True
    started = asyncio.Event()

    async def wait_for_review(*args):
        started.set()
        await asyncio.Event().wait()

    value._client.review_speech.side_effect = wait_for_review
    for name in ["first", "second"]:
        await value.handle_remote_reply(
            {
                "owner_epoch": "owner",
                "reply_id": name,
                "spoken_text": "The answer is ready.",
            }
        )
    await started.wait()
    worker = value._remote_reply_task
    if action == "cancel":
        await value._cancel_output({})
    else:
        await getattr(value, "stop_voice" if action == "stop" else "shutdown")()
    assert worker.cancelled()
    assert value._remote_reply_queue.empty()
    await value._remote_reply_queue.join()
    assert not any(
        call.args[0] == "voice_out" for call in value._client.call.call_args_list
    )
    if action == "cancel":
        value._client.review_speech.side_effect = None
        await value.handle_remote_reply(
            {
                "owner_epoch": "owner",
                "reply_id": "new",
                "spoken_text": "New answer.",
            }
        )
        await value._remote_reply_queue.join()
        assert value._client.call.call_args.kwargs["text"] == "New answer."
        await value.stop_voice()


@pytest.mark.asyncio
async def test_remote_reply_queue_has_explicit_bound_and_rejects_stale_owners():
    value = plugin()
    value.requested = True
    value._spoken_reply = AsyncMock()
    await value.handle_remote_reply(
        {
            "owner_epoch": "old",
            "reply_id": "old",
            "spoken_text": "Old reply.",
        }
    )
    assert value._remote_reply_queue.empty()
    for i in range(33):
        await value.handle_remote_reply(
            {
                "owner_epoch": "owner",
                "reply_id": str(i),
                "spoken_text": "Queued reply.",
            }
        )
    assert value.status()["pending_replies"] == 32
    assert "queue is full" in value.status()["output"]["error"]
    # A provider/context change invalidates work queued before the switch.
    value._classifier_generation += 1
    await value._remote_reply_queue.join()
    value._spoken_reply.assert_not_awaited()
    await value.stop_voice()


@pytest.mark.asyncio
async def test_only_spoken_field_reaches_classifier_and_device_and_duplicates_do_not_replay():
    value = plugin()
    data = reply("The example is on screen.")
    await value._spoken_reply(data)
    await value._spoken_reply(data)
    value._client.review_speech.assert_awaited_once_with(
        "The example is on screen.", "owner", "capability"
    )
    value._client.call.assert_awaited_once()
    assert value._client.call.call_args.kwargs["text"] == "The example is on screen."
    assert "private_example" not in repr(value._client.call.call_args)
    value._rewrite_speech.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("spoken", ["", ".", " \n. "])
async def test_explicit_silence_never_uses_display_fallback(spoken):
    value = plugin()
    await value._spoken_reply(reply(spoken))
    value._client.review_speech.assert_not_awaited()
    value._client.call.assert_not_awaited()
    value._rewrite_speech.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_spoken_field_is_created_on_host_before_classification():
    value = plugin()
    await value._spoken_reply(reply(None))
    assert "private_example" in value._rewrite_speech.call_args.args[0]
    value._client.review_speech.assert_awaited_once_with(
        "The short answer is ready.", "owner", "capability"
    )
    assert "private_example" not in repr(value._client.call.call_args)


@pytest.mark.asyncio
async def test_uncertain_laya_style_gets_tool_free_provider_review_of_spoken_only():
    value = plugin()
    value._client.review_speech.return_value = SpeechDecision(
        "defer", "Laya is unsure", "laya", confidence=0.56
    )
    value._speech_request = AsyncMock(
        return_value='{"decision":"speak","reason":"Brief conversational reply"}'
    )
    await value._spoken_reply(reply("The code example is on your screen."))
    request = value._speech_request.call_args.args[1]
    assert request == {
        "spoken_text": "The code example is on your screen.",
        "classifier_note": "Laya is unsure",
    }
    assert "private_example" not in repr(value._speech_request.call_args)
    assert value._last_speech_decision["provider"] == "laya+provider"
    value._client.call.assert_awaited_once()
    value._rewrite_speech.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_style_provider_failure_does_not_speak_or_retry_forever():
    value = plugin()
    value._client.review_speech.return_value = SpeechDecision("defer", provider="laya")
    value._speech_request = AsyncMock(side_effect=RuntimeError("Review unavailable"))
    await value._spoken_reply(reply("The code example is on your screen."))
    value._client.call.assert_not_awaited()
    value._rewrite_speech.assert_not_awaited()
    assert "Review unavailable" in value.status()["output"]["error"]


@pytest.mark.asyncio
async def test_uncertain_rewrite_is_reviewed_without_spending_another_rewrite():
    value = plugin()
    value._client.review_speech.return_value = SpeechDecision("defer", provider="laya")
    value._speech_request = AsyncMock(
        return_value='{"decision":"speak","reason":"Brief conversational reply"}'
    )
    await value._spoken_reply(reply("One sentence. Two sentences. Three sentences."))
    value._rewrite_speech.assert_awaited_once()
    value._speech_request.assert_awaited_once()
    value._client.call.assert_awaited_once()


@pytest.mark.asyncio
async def test_laya_rejected_rewrite_gets_one_provider_review_of_spoken_only():
    value = plugin()
    value._client.review_speech.return_value = SpeechDecision(
        "rewrite",
        "Use brief conversational language suitable for speech",
        "laya",
        confidence=0.99,
    )
    value._speech_request = AsyncMock(
        return_value='{"decision":"speak","reason":"Brief conversational reply"}'
    )
    await value._spoken_reply(reply("I found the cause. I am checking the fix."))
    value._rewrite_speech.assert_awaited_once()
    value._speech_request.assert_awaited_once()
    assert (
        value._speech_request.call_args.args[1]["spoken_text"]
        == "The short answer is ready."
    )
    assert "private_example" not in repr(value._speech_request.call_args)
    assert value._last_speech_decision["provider"] == "laya+provider"
    value._client.call.assert_awaited_once()
    assert value._client.call.call_args.kwargs["text"] == "The short answer is ready."
    assert value._speech_error is None


@pytest.mark.asyncio
async def test_attached_rewrite_gets_provider_review_without_another_rewrite():
    value = plugin()
    value._client.review_speech.return_value = SpeechDecision(
        "rewrite", "Too technical", "laya"
    )
    value._speech_request = AsyncMock(return_value='{"decision":"speak"}')
    await value._spoken_reply(reply("The short answer is ready.", rewrite_count=1))
    value._rewrite_speech.assert_not_awaited()
    value._speech_request.assert_awaited_once()
    value._client.call.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["rewrite", "unavailable"])
async def test_rejected_rewrite_does_not_block_the_next_queued_reply(decision):
    value = plugin()
    value.requested = True
    value._client.review_speech.side_effect = [
        SpeechDecision("rewrite", "Too technical", "laya"),
        SpeechDecision("speak", provider="laya"),
    ]
    value._speech_request = AsyncMock(
        return_value='{"decision":"rewrite","reason":"Still too technical"}',
        side_effect=RuntimeError("Provider unavailable")
        if decision == "unavailable"
        else None,
    )
    await value.handle_remote_reply(
        {
            "owner_epoch": "owner",
            "reply_id": "rejected",
            "spoken_text": "The short answer is ready.",
            "rewrite_count": 1,
        }
    )
    await value._remote_reply_queue.join()
    value._speech_request.assert_awaited_once()
    assert "skipped" in value.status()["output"]["error"]
    value._client.call.assert_not_awaited()
    assert value._observer_error is None
    await value.handle_remote_reply(
        {
            "owner_epoch": "owner",
            "reply_id": "next",
            "spoken_text": "Yes, I can hear you.",
        }
    )
    await value._remote_reply_queue.join()
    assert value._client.call.call_args.kwargs["text"] == "Yes, I can hear you."
    assert value._speech_error is None
    value._rewrite_speech.assert_not_awaited()


@pytest.mark.asyncio
async def test_previous_output_error_clears_when_the_next_review_starts():
    value = plugin()
    value._speech_error = "Spoken reply skipped: old rejection"
    started, release = asyncio.Event(), asyncio.Event()

    async def review(*args):
        started.set()
        await release.wait()
        return SpeechDecision("speak", provider="laya")

    value._client.review_speech.side_effect = review
    task = asyncio.create_task(value._spoken_reply(reply("Yes, I can hear you.")))
    try:
        await started.wait()
        assert value._speech_error is None
        assert value.status()["speech_review_state"] == "checking spoken reply"
    finally:
        release.set()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("attached", [False, True])
async def test_admitted_reply_outlives_input_deadline_but_still_requires_live_owner(
    attached,
):
    value = plugin()
    del value.validate_turn
    value.requested = True
    published = []
    value._event_bus = SimpleNamespace(
        get_service=lambda name: (
            SimpleNamespace(publish=lambda *a, **kw: published.append((a, kw)))
            if name == "display_tap"
            else None
        ),
    )
    if attached:
        value._remote_binding = {"epoch": "owner", "heartbeat": time.monotonic()}
    data = reply("The check is complete.")
    data["voice"]["expires_at"] = time.time() - 300
    await value._spoken_reply(data)
    assert value._speech_error is None
    if attached:
        assert published[0][0][0]["spoken_text"] == "The check is complete."
        assert "private_example" not in repr(published)
        assert len(published) == 1
    else:
        assert value._client.call.call_args.kwargs["text"] == "The check is complete."
    # A current input deadline must never authorize an old owner.
    value._lease["epoch"] = "another-owner"
    value._remote_binding = None
    data["voice"]["reply_id"] = "stale-owner"
    data["voice"]["expires_at"] = time.time() + 30
    await value._spoken_reply(data)
    assert "inactive conversation" in value._speech_error


@pytest.mark.asyncio
async def test_long_spoken_reply_gets_one_nudge_then_only_approved_rewrite_plays():
    value = plugin()
    await value._spoken_reply(reply("One sentence. Two sentences. Three sentences."))
    value._rewrite_speech.assert_awaited_once()
    value._client.call.assert_awaited_once()
    assert value._client.call.call_args.kwargs["text"] == "The short answer is ready."


@pytest.mark.asyncio
async def test_rejected_rewrite_stays_silent_and_does_not_loop():
    value = plugin()
    value._rewrite_speech.return_value = "Still long. Still formatted. Still wrong."
    value._speech_request = AsyncMock()
    await value._spoken_reply(reply("One. Two. Three."))
    value._rewrite_speech.assert_awaited_once()
    value._client.call.assert_not_awaited()
    value._speech_request.assert_not_awaited()
    assert "after one rewrite" in value.status()["output"]["error"]


@pytest.mark.asyncio
async def test_attached_rewrite_budget_cannot_restart_on_the_device():
    value = plugin()
    await value._spoken_reply(reply("One. Two. Three.", rewrite_count=1))
    value._rewrite_speech.assert_not_awaited()
    value._client.call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", ["cancel", "classifier"])
async def test_late_review_cannot_speak_after_cancellation_or_classifier_switch(cancel):
    value = plugin()
    started, release = asyncio.Event(), asyncio.Event()

    async def review(*args):
        started.set()
        await release.wait()
        return SpeechDecision("speak", provider="laya")

    value._client.review_speech.side_effect = review
    task = asyncio.create_task(value._spoken_reply(reply("The tests passed.")))
    await started.wait()
    if cancel == "cancel":
        value._output_generation += 1
    else:
        value._classifier_generation += 1
    release.set()
    await task
    value._client.call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", ["cancel", "classifier", "owner"])
async def test_provider_adjudication_cannot_speak_after_cancellation_or_owner_change(
    cancel,
):
    value = plugin()
    del value.validate_turn
    value.requested = True
    value._client.review_speech.return_value = SpeechDecision(
        "rewrite", "Too technical", "laya"
    )
    started, release = asyncio.Event(), asyncio.Event()

    async def review(*args):
        started.set()
        await release.wait()
        return '{"decision":"speak"}'

    value._speech_request = review
    task = asyncio.create_task(
        value._spoken_reply(reply("The check is complete.", rewrite_count=1))
    )
    await started.wait()
    if cancel == "cancel":
        value._output_generation += 1
    elif cancel == "classifier":
        value._classifier_generation += 1
    else:
        value._lease["epoch"] = "another-owner"
    release.set()
    await task
    assert not any(
        call.args[0] == "voice_out" for call in value._client.call.call_args_list
    )


@pytest.mark.asyncio
async def test_provider_classifier_keeps_single_rewrite_budget():
    value = plugin()
    value.classifier_name = "provider"
    value._speech_request = AsyncMock(
        return_value='{"decision":"rewrite","reason":"Too technical"}'
    )
    await value._spoken_reply(reply("The check is complete."))
    value._client.review_speech.assert_not_awaited()
    value._rewrite_speech.assert_awaited_once()
    assert value._speech_request.await_count == 2
    value._client.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_rewrite_provider_request_has_no_tools_and_reuses_one_client(monkeypatch):
    calls = []
    created = []

    class API:
        def __init__(self, *args):
            created.append(self)

        async def initialize(self):
            return True

        async def call_llm(self, messages, **kwargs):
            calls.append((messages, kwargs))
            return "The example is on screen."

        async def shutdown(self):
            pass

    import kollabor_ai.api_communication_service as api_module

    monkeypatch.setattr(api_module, "APICommunicationService", API)
    profile = SimpleNamespace(to_dict=lambda: {"provider": "test"})
    llm = SimpleNamespace(api_service=SimpleNamespace(_profile=profile))
    value = VoicePlugin(
        event_bus=SimpleNamespace(
            get_service=lambda n: llm if n == "llm_service" else None
        ),
        config={},
    )
    for _ in range(2):
        await value._rewrite_speech(
            "Full answer with a code example.", "missing spoken_text"
        )
    assert len(created) == 1
    assert all(kwargs == {"tools": []} for _, kwargs in calls)
    assert "Do not perform actions" in calls[0][0][0]["content"]
