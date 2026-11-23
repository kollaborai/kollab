import asyncio
from types import SimpleNamespace

import pytest

from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor.llm.llm_coordinator import LLMService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reserve_cancel", [True, False], ids=["cancel-reserved", "cancel-refused"]
)
async def test_send_now_fences_owner_claimed_before_turn_start_marker(reserve_cancel):
    runner = ACPRunner(ACPProfile("startup-owner", ("unused",)))
    runner.session_id = "session-A"
    runner.generation = 7
    started = asyncio.Event()
    release = asyncio.Event()
    prompts = []

    class Connection:
        async def prompt(self, *, session_id, prompt):
            prompts.append(prompt[0].text)
            if len(prompts) == 1:
                started.set()
                await release.wait()
            return SimpleNamespace(stopReason="end_turn", usage=None)

    runner._connection = Connection()
    owner_admission = runner.admit("active A", request_id="active-A", source="rpc")
    runner.admit("older D", request_id="older-D", source="rpc")
    cancel_scheduled = []

    async def send_now_as_soon_as_owner_is_claimed():
        while getattr(runner, "_active_context", None) is not owner_admission:
            await asyncio.sleep(0)
        # _drain_queue has claimed A, but its scheduled _run_prompt task has
        # not yet initialized _active_turn_id/_active_generation.
        assert getattr(runner, "_active_context", None) is owner_admission
        assert runner._active_turn_id == 0
        result = await LLMService._admit_acp(
            object.__new__(LLMService),
            SimpleNamespace(
                active=True,
                runner=runner,
                supports_image_input=False,
                voice_rules_epochs=set(),
                steer=lambda text, *, origin, thread_id, request_id, **kw: runner.steer(
                    text,
                    origin=origin,
                    thread_id=thread_id,
                    images=kw.get("images", ()),
                ),
                admit=runner.admit,
                prioritize_queued=runner.prioritize_queued,
                prepare_cancel=lambda *, session_id, generation: (
                    runner.prepare_cancel(
                        expected_session_id=session_id, expected_generation=generation
                    )
                    if reserve_cancel
                    else False
                ),
                schedule_prepared_cancel=lambda **kw: cancel_scheduled.append(kw)
                or True,
            ),
            {"message": "send now B", "request_id": "client-B", "send_now": True},
            "state_rpc",
        )
        return result

    watcher = asyncio.create_task(send_now_as_soon_as_owner_is_claimed())
    worker = asyncio.create_task(runner._drain_queue())
    try:
        result = await asyncio.wait_for(watcher, timeout=1)
        assert result["status"] == "queued"
        await asyncio.wait_for(started.wait(), timeout=1)
        assert getattr(runner, "_active_context", None) is owner_admission
        assert runner._active_turn_id == owner_admission.turn_id
        assert [item.request_id for item in runner._queue._queue] == [
            "client-B",
            "older-D",
        ]
        assert [item.request_id for item in runner._queue._queue].count("client-B") == 1
        assert runner._cancel_hold is reserve_cancel
        assert len(cancel_scheduled) == int(reserve_cancel)
    finally:
        release.set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.parametrize(
    "owner_change", ["gone", "replaced"], ids=["owner-gone", "owner-replaced"]
)
@pytest.mark.asyncio
async def test_send_now_rejects_when_startup_owner_changes_after_snapshot(owner_change):
    runner = ACPRunner(ACPProfile("startup-owner-switch", ("unused",)))
    runner.session_id = "session-A"
    runner.generation = 7
    runner._active_turn_id = 0
    runner._active_generation = 0
    owner = SimpleNamespace(request_id="active-A", turn_id=11)
    runner._active_context = owner
    entered_steer = asyncio.Event()
    release_steer = asyncio.Event()
    admissions = []
    scheduled_cancels = []

    async def hold_owner():
        await asyncio.Event().wait()

    original_task = asyncio.create_task(hold_owner())
    runner._active_rpc_task = original_task
    replacement_task = None

    class ACP:
        active = True
        supports_image_input = False
        voice_rules_epochs = set()

        def __init__(self):
            self.runner = runner

        async def steer(self, *_args, **_kwargs):
            entered_steer.set()
            await release_steer.wait()
            return False

        def admit(self, _message, **kwargs):
            admissions.append(kwargs)
            return SimpleNamespace(request_id=kwargs["request_id"], turn_id=12)

        def prioritize_queued(self, _admission):
            return True

        def prepare_cancel(self, **_kwargs):
            return runner.prepare_cancel(
                expected_session_id="session-A", expected_generation=7
            )

        def schedule_prepared_cancel(self, **kwargs):
            scheduled_cancels.append(kwargs)
            return True

    request = asyncio.create_task(
        LLMService._admit_acp(
            object.__new__(LLMService),
            ACP(),
            {"message": "send now B", "request_id": "client-B", "send_now": True},
            "state_rpc",
        )
    )
    try:
        await asyncio.wait_for(entered_steer.wait(), timeout=1)
        if owner_change == "gone":
            runner._active_context = None
            runner._active_rpc_task = None
        else:
            replacement = SimpleNamespace(request_id="replacement-C", turn_id=13)
            runner._active_context = replacement
            replacement_task = asyncio.create_task(hold_owner())
            runner._active_rpc_task = replacement_task
            runner._active_turn_id = 13
            runner._active_generation = 8
        runner.generation = 8
        runner.session_id = "session-B"
        release_steer.set()

        result = await asyncio.wait_for(request, timeout=1)

        assert result == {"status": "rejected", "reason": "turn_changed"}
        assert admissions == []
        assert runner._queue.empty()
        assert scheduled_cancels == []
        assert runner._cancel_hold is False
    finally:
        release_steer.set()
        request.cancel()
        original_task.cancel()
        if replacement_task is not None:
            replacement_task.cancel()
        await asyncio.gather(
            request,
            original_task,
            *([replacement_task] if replacement_task else []),
            return_exceptions=True,
        )
