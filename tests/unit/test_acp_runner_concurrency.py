"""A provider failure on one ACP request must not break its neighbours (#714).

The runner restarts its harness after a failed prompt. A request admitted
while that restart runs belongs to the same runner and must wait in the queue
like one admitted before the failure, not be refused as "not started".
"""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from kollabor.llm.acp_bridge import ACPError, ACPProfile, ACPRunner


class _Connection:
    """Fails the first prompt, answers every later one."""

    def __init__(self, fail_first, prompts):
        self._fail_first = fail_first
        self._prompts = prompts

    async def prompt(self, **kwargs):
        self._prompts.append(kwargs["session_id"])
        if self._fail_first and len(self._prompts) == 1:
            raise ConnectionError("provider error")
        return SimpleNamespace(stopReason="end_turn", usage=None)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_request_admitted_while_the_runner_restarts_after_a_failure_is_queued(tmp_path):
    runner = ACPRunner(ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)))
    prompts: list[str] = []
    restarting = asyncio.Event()
    finish_restart = asyncio.Event()
    runner.session_id = "old"
    runner.generation = 1
    runner._connection = _Connection(True, prompts)

    async def terminate():
        # What the real teardown does first: the connection is gone.
        runner._connection = None

    async def start():
        restarting.set()
        await finish_restart.wait()
        runner.session_id = "new"
        runner._connection = _Connection(False, prompts)
        return "new"

    runner._terminate_process = terminate
    runner._start_process = start
    runner._worker = asyncio.create_task(runner._drain_queue())
    try:
        failing = runner.admit("a", request_id="a", source="hub")
        with pytest.raises(ACPError, match="harness request failed"):
            await asyncio.wait_for(failing.future, timeout=1)
        await asyncio.wait_for(restarting.wait(), timeout=1)

        # The failed request is done and the restart is under way. A new
        # request arrives now and must be taken, then answered by the new harness.
        late = runner.admit("c", request_id="c", source="hub")
        finish_restart.set()
        result = await asyncio.wait_for(late.future, timeout=1)
        assert result.session_id == "new"
        assert prompts == ["old", "new"]
    finally:
        finish_restart.set()
        runner._worker.cancel()
        await asyncio.gather(runner._worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_runner_that_never_started_still_refuses_requests(tmp_path):
    runner = ACPRunner(ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)))
    with pytest.raises(ACPError, match="not started"):
        runner.admit("x", request_id="x", source="hub")


@pytest.mark.asyncio
async def test_a_request_queued_before_a_failure_runs_on_the_restarted_harness(tmp_path):
    runner = ACPRunner(ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)))
    prompts: list[str] = []
    runner.session_id = "old"
    runner.generation = 1
    runner._connection = _Connection(True, prompts)

    async def terminate():
        runner._connection = None

    async def start():
        runner.session_id = "new"
        runner._connection = _Connection(False, prompts)
        return "new"

    runner._terminate_process = terminate
    runner._start_process = start
    runner._worker = asyncio.create_task(runner._drain_queue())
    try:
        first = runner.admit("a", request_id="a", source="hub")
        second = runner.admit("b", request_id="b", source="hub")
        with pytest.raises(ACPError):
            await asyncio.wait_for(first.future, timeout=1)
        assert (await asyncio.wait_for(second.future, timeout=1)).session_id == "new"
        assert prompts == ["old", "new"]
    finally:
        runner._worker.cancel()
        await asyncio.gather(runner._worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_request_admitted_during_a_restart_that_fails_gets_that_error_and_does_not_hang(tmp_path):
    runner = ACPRunner(ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)))
    prompts: list[str] = []
    restarting = asyncio.Event()
    finish_restart = asyncio.Event()
    runner.session_id = "old"
    runner.generation = 1
    runner._connection = _Connection(True, prompts)

    async def terminate():
        runner._connection = None

    async def start():
        restarting.set()
        await finish_restart.wait()
        raise ACPError("harness would not restart")

    runner._terminate_process = terminate
    runner._start_process = start
    runner._worker = asyncio.create_task(runner._drain_queue())
    try:
        failing = runner.admit("a", request_id="a", source="hub")
        with pytest.raises(ACPError, match="harness request failed"):
            await asyncio.wait_for(failing.future, timeout=1)
        await asyncio.wait_for(restarting.wait(), timeout=1)
        late = runner.admit("c", request_id="c", source="hub")
        finish_restart.set()
        with pytest.raises(ACPError, match="would not restart"):
            await asyncio.wait_for(late.future, timeout=1)
        # The restart is over and failed: the runner is no longer taking requests.
        assert not runner._restarting
        with pytest.raises(ACPError, match="not started"):
            runner.admit("d", request_id="d", source="hub")
        assert prompts == ["old"]
    finally:
        finish_restart.set()
        runner._worker.cancel()
        await asyncio.gather(runner._worker, return_exceptions=True)
