"""Decision contract, worker lifecycle and setup isolation checks."""

import asyncio
import json
import sys

import pytest
from kollabor_voice.classifiers import (
    DecisionRequest,
    ProviderClassifier,
    VoiceDecision,
    load_classifier,
    save_classifier,
)
from kollabor_voice.control import VoiceError
from kollabor_voice.laya import LayaWorker


def test_classifier_default_and_corrupt_preference(tmp_path):
    assert load_classifier(tmp_path) == "laya"
    (tmp_path / "preferences.json").write_text("{broken")
    assert load_classifier(tmp_path) == "laya"
    (tmp_path / "preferences.json").write_text(json.dumps({"unrelated": True}))
    save_classifier("provider", tmp_path)
    assert load_classifier(tmp_path) == "provider"
    assert json.loads((tmp_path / "preferences.json").read_text())["unrelated"]


@pytest.mark.parametrize(
    "decision",
    [
        VoiceDecision("respond", ["missing"], "laya"),
        VoiceDecision("respond", [], "laya"),
        VoiceDecision("ignore", ["a"], "laya"),
        VoiceDecision("respond", ["a", "a"], "laya"),
        VoiceDecision("respond", ["a"], "laya", confidence=float("nan")),
        VoiceDecision("pending", [], "laya", detail="uncertain"),
        VoiceDecision("defer", ["a"], "laya"),
        VoiceDecision("respond", ["a"], "laya", deferred_event_ids=["missing"]),
    ],
)
def test_invalid_decisions_never_drop_or_admit_words(decision):
    with pytest.raises(VoiceError):
        decision.selected([{"event_id": "a", "text": "Keep this."}])


@pytest.mark.asyncio
async def test_provider_adapter_obeys_same_contract():
    records = [{"event_id": "a", "text": "Please help."}]

    async def observe(batch, transcript_context):
        assert transcript_context == []
        return batch

    decision = await ProviderClassifier(observe).decide(DecisionRequest(records))
    assert decision.provider == "provider"
    assert VoiceDecision.from_wire(decision.to_wire()).selected(records) == records


@pytest.mark.asyncio
async def test_fifty_prepare_calls_load_one_worker(tmp_path):
    worker = LayaWorker(tmp_path)
    gate = asyncio.Event()
    started = []

    async def prepare():
        started.append(True)
        await gate.wait()
        worker.state = {"state": "ready"}

    worker._prepare = prepare
    for _ in range(50):
        worker.prepare()
    await asyncio.sleep(0)
    assert len(started) == 1
    gate.set()
    await worker.setup_task
    worker.prepare()
    assert len(started) == 1
    await worker.close()


@pytest.mark.asyncio
async def test_persistent_worker_serializes_and_reuses_process_and_closes(tmp_path):
    worker = LayaWorker(tmp_path)
    code = """import sys,json
for line in sys.stdin:
 r=json.loads(line)
 print(json.dumps({'id':r['id'],'result':{'event_ids':[x['event_id'] for x in r['records']]}}),flush=True)
"""
    worker.process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    process = worker.process
    worker.state = {"state": "ready"}
    results = await asyncio.gather(
        *(worker.decide([{"event_id": str(i)}], "") for i in range(10))
    )
    assert [r["event_ids"] for r in results] == [[str(i)] for i in range(10)]
    assert worker.process.pid == process.pid and process.returncode is None
    await worker.close()
    assert process.returncode is not None


@pytest.mark.asyncio
async def test_worker_protocol_error_is_terminal_and_never_reused(tmp_path):
    worker = LayaWorker(tmp_path)
    worker.process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        'import sys; sys.stdin.readline(); print(\'{"id":"wrong"}\',flush=True); sys.stdin.read()',
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    process = worker.process
    worker.state = {"state": "ready"}
    with pytest.raises(ValueError, match="identity"):
        await worker.decide([{"event_id": "a"}], "")
    assert process.returncode is not None and worker.status()["state"] == "error"


@pytest.mark.asyncio
async def test_setup_failure_is_visible_and_retry_starts_one_new_attempt(tmp_path):
    worker = LayaWorker(tmp_path)
    attempts = []

    async def setup():
        attempts.append(True)
        worker.state = {"state": "error", "detail": "Missing model"}

    worker._prepare = setup
    worker.prepare()
    await worker.setup_task
    assert worker.status()["state"] == "error"
    worker.prepare()
    await worker.setup_task
    assert len(attempts) == 2
    await worker.close()


@pytest.mark.parametrize("cause", ["uncertain", "language", "too_long"])
def test_deferred_group_reaches_agent_alongside_a_separate_confident_request(cause):
    from types import SimpleNamespace

    from kollabor_voice.laya_worker import predict

    answers = iter([0.55, 0.99] if cause == "uncertain" else [0.99])

    def classify(*args):
        p = next(answers, 0.99)
        return {
            "answers": {
                "response": {
                    "choice": "respond",
                    "probabilities": {"respond": p, "ignore": 1 - p},
                }
            }
        }

    agent = SimpleNamespace(
        cfg={"max_len": 1024, "head_max_len": 256},
        tok=SimpleNamespace(
            encode=lambda state: [0]
            * (1024 if cause == "too_long" and "Unclear" in state else 20)
        ),
        predict=classify,
    )
    records = [
        {
            "event_id": "ambient",
            "text": "Unclear ambient text.",
            "language": "es" if cause == "language" else "en",
            "started_at": "2026-09-26T00:00:00+00:00",
            "ended_at": "2026-09-26T00:00:01+00:00",
        },
        {
            "event_id": "request",
            "text": "Please check the readme.",
            "started_at": "2026-09-26T00:00:05+00:00",
            "ended_at": "2026-09-26T00:00:06+00:00",
        },
    ]
    result = predict(agent, records, "")
    assert result.selected(records) == records
    # A preceding oversized line also belongs to the next group's history. It
    # must reach the agent intact rather than silently disappear to make it fit.
    assert result.deferred_event_ids == (
        ["ambient", "request"] if cause == "too_long" else ["ambient"]
    )
    assert result.decision == ("defer" if cause == "too_long" else "respond")


def test_laya_receives_exact_playback_and_defers_uncertain_echo_to_agent():
    from types import SimpleNamespace

    from kollabor_voice.laya_worker import predict

    states = []

    def classify(state, _):
        states.append(state)
        return {
            "answers": {
                "response": {
                    "choice": "ignore",
                    "probabilities": {"respond": 0.4, "ignore": 0.6},
                }
            }
        }

    agent = SimpleNamespace(
        cfg={"max_len": 1024, "head_max_len": 256},
        tok=SimpleNamespace(encode=lambda _: [0]),
        predict=classify,
    )
    record = {
        "event_id": "heard",
        "text": "Should I run the tests?",
        "playback_overlap": [{"reply_id": "reply", "text": "Should I run the tests?"}],
        "started_at": "2026-09-26T00:00:00+00:00",
        "ended_at": "2026-09-26T00:00:01+00:00",
    }
    result = predict(agent, [record], "")
    assert states[0]["assistant_playback"] == [record["text"]]
    assert result.decision == "defer" and result.selected([record]) == [record]
    assert result.deferred_event_ids == ["heard"]


def test_long_conversation_context_never_displaces_current_utterance():
    from types import SimpleNamespace

    from kollabor_voice.laya_worker import predict

    observed = []

    def classify(state, questions):
        observed.append(state.copy())
        return {
            "answers": {
                "response": {
                    "choice": "respond",
                    "probabilities": {"respond": 0.99, "ignore": 0.01},
                }
            }
        }

    agent = SimpleNamespace(
        cfg={"max_len": 300, "head_max_len": 64},
        tok=SimpleNamespace(encode=lambda state: list(state)),
        predict=classify,
    )
    record = {
        "event_id": "request",
        "text": "First the lead-in. Please check the readme.",
        "started_at": "2026-09-26T00:00:00+00:00",
        "ended_at": "2026-09-26T00:00:01+00:00",
    }
    result = predict(agent, [record], "An old discussion. " * 100)
    assert result.selected([record]) == [record]
    assert observed[0]["text"] == record["text"]
    assert len(json.dumps(observed[0])) <= 204
    assert observed[0]["recent_conversation"]
