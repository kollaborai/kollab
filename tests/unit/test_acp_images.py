from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.llm.acp_bridge import ACPError, ACPProfile, ACPRunner
from kollabor.llm.llm_coordinator import LLMService
from kollabor.state import LocalStateService


def runner_with_capability(supported):
    runner = ACPRunner(ACPProfile("test", ("test",)))
    runner.session_id = "session"
    runner.capabilities = {"promptCapabilities": {"image": supported}}
    runner._connection = SimpleNamespace(
        prompt=AsyncMock(return_value=SimpleNamespace(stopReason="end_turn")),
        ext_method=AsyncMock(),
    )
    return runner


@pytest.mark.asyncio
async def test_image_input_is_normalized_and_sent_as_acp_blocks_without_history_bytes():
    runner = runner_with_capability(True)
    acp = SimpleNamespace(
        supports_image_input=True,
        steer=AsyncMock(return_value=False),
        admit=runner.admit,
    )
    service = object.__new__(LLMService)
    result = await service._admit_acp(acp, {
        "message": [
            {"type": "text", "text": "Describe the screenshots"},
            {"type": "image", "image": "data:image/png;base64,YWJj"},
            {"type": "image", "image": "data:image/jpeg;base64,ZGVm"},
        ],
        "request_id": "image-request",
    }, "state_rpc")

    assert result["status"] == "queued"
    admission = runner._queue.get_nowait()
    assert "YWJj" not in repr(admission)
    assert "YWJj" not in admission.text
    await runner._run_prompt(admission, runner.generation)
    blocks = runner._connection.prompt.await_args.kwargs["prompt"]
    wire = [block.model_dump(by_alias=True, exclude_none=True) for block in blocks]
    assert wire[0]["type"] == "text"
    assert "Describe the screenshots" in wire[0]["text"]
    assert wire[1:] == [
        {"type": "image", "data": "YWJj", "mimeType": "image/png"},
        {"type": "image", "data": "ZGVm", "mimeType": "image/jpeg"},
    ]


@pytest.mark.asyncio
async def test_unsupported_harness_rejects_images_before_queue_or_prompt():
    runner = runner_with_capability(False)
    acp = SimpleNamespace(supports_image_input=False, steer=AsyncMock(), admit=runner.admit)
    service = object.__new__(LLMService)
    result = await service._admit_acp(acp, {
        "message": [{"type": "text", "text": "Look"},
                    {"type": "image", "image": "data:image/png;base64,YWJj"}],
    }, "state_rpc")
    assert result == {"status": "rejected", "reason": "This harness can't take images."}
    assert runner._queue.empty()
    runner._connection.prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_queued_image_is_not_sent_if_restarted_harness_loses_capability():
    runner = runner_with_capability(True)
    admission = runner.admit(
        "look", request_id="request", source="state_rpc",
        images=({"data": "YWJj", "mime_type": "image/png"},),
    )
    runner.capabilities = {"promptCapabilities": {"image": False}}
    with pytest.raises(ACPError, match="can't take images"):
        await runner._run_prompt(admission, runner.generation)
    runner._connection.prompt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("during_steering", [True, False])
async def test_capability_loss_during_admission_returns_the_image_notice(during_steering):
    runner = runner_with_capability(False)
    steer = (
        AsyncMock(side_effect=ACPError("This harness can't take images."))
        if during_steering else AsyncMock(return_value=False)
    )
    acp = SimpleNamespace(supports_image_input=True, steer=steer, admit=runner.admit)
    service = object.__new__(LLMService)
    result = await service._admit_acp(acp, {
        "message": [{"type": "text", "text": "Look"},
                    {"type": "image", "image": "data:image/png;base64,YWJj"}],
    }, "state_rpc")
    assert result == {"status": "rejected", "reason": "This harness can't take images."}
    assert runner._queue.empty()


@pytest.mark.asyncio
async def test_image_url_is_rejected_instead_of_dropped():
    runner = runner_with_capability(True)
    acp = SimpleNamespace(supports_image_input=True, steer=AsyncMock(), admit=runner.admit)
    service = object.__new__(LLMService)
    result = await service._admit_acp(acp, {
        "message": [{"type": "text", "text": "Look"},
                    {"type": "image", "image": "https://example.com/image.png"}],
    }, "state_rpc")
    assert result == {"status": "rejected", "reason": "Image unavailable or unsupported; upload it and retry."}
    assert runner._queue.empty()


@pytest.mark.asyncio
async def test_steering_carries_images_and_rechecks_capability():
    runner = runner_with_capability(True)
    runner.initialize_metadata = {"steering": {"supported": True}}
    runner._active_turn_id = 1
    runner._active_context = SimpleNamespace(thread_id=None, origin={})
    image = {"data": "YWJj", "mime_type": "image/png"}
    assert await runner.steer("look", origin={"operator": True}, images=(image,))
    wire = runner._connection.ext_method.await_args.args[1]["prompt"]
    assert wire == [{"type": "text", "text": "look"},
                    {"type": "image", "data": "YWJj", "mimeType": "image/png"}]
    runner.capabilities = {"promptCapabilities": {"image": False}}
    with pytest.raises(ACPError, match="can't take images"):
        await runner.steer("look", origin={"operator": True}, images=(image,))
    assert runner._connection.ext_method.await_count == 1


@pytest.mark.parametrize("capability", [None, {}, {"image": False}, {"image": "true"}])
def test_image_capability_requires_explicit_true(capability):
    runner = runner_with_capability(False)
    runner.capabilities = {"promptCapabilities": capability}
    assert runner.supports_image_input is False


def test_active_acp_profile_snapshot_uses_negotiated_vision():
    profile = SimpleNamespace(name="test", provider="acp", model="")
    llm = SimpleNamespace(supports_image_input=lambda: True)
    state = LocalStateService(llm_service=llm, profile_manager=None)
    assert state._profile_to_snapshot(profile, is_active=True).supports_vision is True
    assert state._profile_to_snapshot(profile, is_active=False).supports_vision is False
