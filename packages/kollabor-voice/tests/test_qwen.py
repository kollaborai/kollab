"""Fine-tuned Qwen3-0.6B classifier: policy, prompt pin, worker lifecycle, routing.

A fake model stands in for MLX here; bench/qwen_voice.py `score runtime`
measures the real weights through the same functions.
"""

import asyncio
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio
from kollabor_voice import speech_settings
from kollabor_voice.classifiers import (
    LocalClassifier,
    VoiceDecision,
    available_classifiers,
    load_preferences,
    save_preferences,
)
from kollabor_voice.client import VoiceClient
from kollabor_voice.control import VoiceError, manifest
from kollabor_voice.qwen import QwenWorker
from kollabor_voice.qwen_worker import (
    messages,
    predict,
    predict_answer,
    predict_speech,
    prompt_sha256,
    system_prompts,
)
from kollabor_voice.service import VoiceService


class Model:
    """Answers each prompt from a table keyed by the text it judges."""

    def __init__(self, table):
        self.table, self.seen = table, []

    def probabilities(self, case):
        self.seen.append(case)
        return self.table[case["text"]]


def record(event_id, text, **fields):
    return {
        "event_id": event_id,
        "text": text,
        "language": "en",
        "owner_epoch": "owner",
        "started_at": "2026-10-19T10:00:00+00:00",
        "ended_at": "2026-10-19T10:00:01+00:00",
        **fields,
    }


def test_intent_decides_at_threshold_and_defers_below_it():
    model = Model(
        {
            "Run the tests.": {"respond": 0.97, "ignore": 0.03},
            "Back to the studio.": {"respond": 0.02, "ignore": 0.98},
            "Hmm, maybe later.": {"respond": 0.6, "ignore": 0.4},
        }
    )
    sure = predict(model, [record("a", "Run the tests.")], "")
    assert (sure.decision, sure.event_ids, sure.provider) == ("respond", ["a"], "qwen")
    assert sure.model == "qwen3-0.6b-voice-v1" and sure.confidence == 0.97
    assert sure.trace[0]["passes"] == [
        {"label": "with context", "choice": "respond", "probability": 0.97}
    ]
    ignored = predict(model, [record("b", "Back to the studio.")], "")
    assert (ignored.decision, ignored.event_ids) == ("ignore", [])
    unsure = predict(model, [record("c", "Hmm, maybe later.")], "")
    assert unsure.decision == "defer" and unsure.deferred_event_ids == ["c"]
    assert "Qwen is uncertain" in unsure.detail
    assert unsure.selected([record("c", "Hmm, maybe later.")])


def test_separate_utterances_decide_alone_and_english_only():
    model = Model(
        {
            "Run the tests.": {"respond": 0.97, "ignore": 0.03},
            "Hmm, maybe later.": {"respond": 0.6, "ignore": 0.4},
        }
    )
    # More than two seconds apart: three utterances.
    at = ["2026-10-19T10:00:09+00:00", "2026-10-19T10:00:20+00:00"]
    batch = [
        record("a", "Run the tests."),
        record("b", "Hmm, maybe later.", started_at=at[0], ended_at=at[0]),
        record("c", "Hola, ¿qué tal?", language="es", started_at=at[1], ended_at=at[1]),
    ]
    decision = predict(model, batch, "")
    assert decision.decision == "respond"
    assert decision.event_ids == ["a", "b", "c"]
    assert decision.deferred_event_ids == ["b", "c"]
    assert "trained for English" in decision.detail
    assert [case["text"] for case in model.seen] == [
        "Run the tests.",
        "Hmm, maybe later.",
    ]


def test_intent_reads_history_playback_and_the_configured_window():
    model = Model({"Stop, wait.": {"respond": 0.95, "ignore": 0.05}})
    history = [record(str(i), f"line {i}") for i in range(12)]
    current = record(
        "now",
        "Stop, wait.",
        playback_overlap=[{"reply_id": "r", "text": "The build passed."}] * 2,
    )
    decision = predict(model, [current], "user: hi\nassistant: hello", history, 12)
    case = model.seen[0]
    assert case["transcript_context"] == [f"line {i}" for i in range(12)]
    assert case["assistant_playback"] == ["The build passed."]
    assert case["context"] == "user: hi\nassistant: hello"
    assert decision.context_event_ids == [r["event_id"] for r in history]
    with pytest.raises(ValueError, match="window"):
        predict(model, [current], "", history, 10)


def test_prompt_layout_is_the_trained_one():
    system = system_prompts()
    case = {
        "task": "intent",
        "text": "Stop, wait.",
        "context": "user: hi",
        "transcript_context": ["x" * 250, "second"],
        "assistant_playback": ["One.", "Two."],
    }
    user = messages(case, system)[1]["content"]
    assert user == (
        "Recent chat:\nuser: hi\n\n"
        f"Earlier microphone lines, oldest first:\n- {'x' * 200}\n- second\n\n"
        'The assistant was speaking at the time: "One." "Two."\n\n'
        'Heard now: "Stop, wait."'
    )
    answer = messages(
        {
            "task": "answer",
            "held": "Check it.",
            "question": "Sorry, was that for me?",
            "text": "Yes.",
        },
        system,
    )
    assert answer[0]["content"].endswith("Answer with one word: yes, no or other.")
    assert answer[1]["content"] == (
        'The speech it asked about: "Check it."\n'
        'The assistant asked: "Sorry, was that for me?"\n'
        'The reply: "Yes."'
    )


def test_prompts_are_pinned_to_the_trained_weights():
    # LAYA_QUESTION and SPEECH_QUESTION feed these prompts: changing either, or
    # the labels, needs new weights and a new pin.
    assert prompt_sha256() == manifest()["classifiers"]["qwen"]["prompt_sha256"]


def test_manifest_pins_every_weight_file_and_the_measured_runtime():
    spec = manifest()["classifiers"]["qwen"]
    assert spec["runtime"] == [
        "mlx==0.32.4",
        "mlx-metal==0.32.4",
        "mlx-lm==0.32.2",
        "transformers==5.20.1",
        "tokenizers==0.23.3",
        "numpy==2.5.4",
    ]
    assert spec["minimum_probability"] == 0.8
    bundle = spec["bundle"]
    assert bundle["license"] == "Apache-2.0" and bundle["revision"]
    names = {item["name"] for item in bundle["files"]}
    assert {
        "config.json",
        "model.safetensors",
        "tokenizer.json",
        "chat_template.jinja",
    } <= names
    assert sum(item["size"] for item in bundle["files"]) == 644921615


def test_answer_takes_the_argmax_and_style_defers_when_unsure():
    model = Model(
        {
            "Nah, that was the TV.": {"yes": 0.4, "no": 0.35, "other": 0.25},
            "All checks passed.": {"say": 0.99, "rewrite": 0.01},
            "The resolver memoizes the AST.": {"say": 0.1, "rewrite": 0.9},
            "Maybe.": {"say": 0.55, "rewrite": 0.45},
        }
    )
    result = predict_answer(
        model, "Check it.", "Sorry, was that for me?", "Nah, that was the TV."
    )
    assert result["answer"] == "yes" and result["provider"] == "qwen"
    assert model.seen[-1]["held"] == "Check it."
    said = predict_speech(model, "All checks passed.")
    assert (said.decision, said.provider, said.confidence) == ("speak", "qwen", 0.99)
    assert predict_speech(model, "The resolver memoizes the AST.").decision == "rewrite"
    assert predict_speech(model, "Maybe.").decision == "defer"
    # The format rule decides first; the model never sees code.
    assert predict_speech(model, "Run `make test`.").provider == "format"
    assert len(model.seen) == 4


def test_qwen_is_offered_and_saved_only_on_apple_silicon(tmp_path, monkeypatch):
    monkeypatch.setattr(speech_settings, "apple_silicon", lambda: False)
    assert "qwen" not in available_classifiers()
    with pytest.raises(ValueError, match="Apple silicon"):
        save_preferences({"classifier": "qwen"}, tmp_path)
    assert not (tmp_path / "preferences.json").exists()
    # A saved choice that this machine cannot run reads as the default.
    (tmp_path / "preferences.json").write_text(json.dumps({"classifier": "qwen"}))
    assert load_preferences(tmp_path)["classifier"] == "laya"
    monkeypatch.setattr(speech_settings, "apple_silicon", lambda: True)
    assert list(available_classifiers()) == ["laya", "qwen", "provider"]
    assert load_preferences(tmp_path)["classifier"] == "qwen"
    (tmp_path / "preferences.json").unlink()
    assert load_preferences(tmp_path)["classifier"] == "laya"  # default unchanged


@pytest.mark.asyncio
async def test_worker_refuses_other_machines_and_answers_over_its_pipe(
    tmp_path, monkeypatch
):
    import kollabor_voice.qwen as qwen

    monkeypatch.setattr(qwen, "apple_silicon", lambda: False)
    worker = QwenWorker(tmp_path)
    worker.prepare()
    assert worker.status()["state"] == "error" and worker.setup_task is None
    assert "Apple silicon" in worker.status()["detail"]
    assert worker.status()["name"] == "qwen"

    code = """import sys,json
for line in sys.stdin:
 r=json.loads(line)
 print(json.dumps({'id':r['id'],'result':{'answer':'no','task':r['task'],'held':r['held']}}),flush=True)
"""
    worker.process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    worker.state = {"state": "ready", "model": worker.model}
    result = await worker.answer("x" * 3000, "Sorry, was that for me?", "No.")
    assert result == {"answer": "no", "task": "answer", "held": "x" * 2000}
    await worker.close()
    # A closed worker is prepared from scratch, never reported ready.
    assert worker.status()["state"] == "off" and worker.status()["pid"] is None


def test_unpublished_weights_name_where_they_go(tmp_path, monkeypatch):
    import kollabor_voice.assets as assets

    content = b"weights"
    bundle = {
        "revision": "voice-qwen-v1",
        "base_url": "",
        "files": [
            {
                "name": "model.safetensors",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        ],
    }

    def offline(*args, **kwargs):
        raise AssertionError("an unpublished bundle never reaches the network")

    monkeypatch.setattr(assets.urllib.request, "urlopen", offline)
    target = tmp_path / "models/qwen/voice-qwen-v1"
    with pytest.raises(
        RuntimeError, match=f"place its verified model.safetensors in {target}"
    ):
        assets.ensure_models(tmp_path, lambda **kw: None, {"qwen": bundle})
    (target / "model.safetensors").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="no download source"):
        assets.ensure_models(tmp_path, lambda **kw: None, {"qwen": bundle})
    (target / "model.safetensors").write_bytes(content)
    assert assets.ensure_models(tmp_path, lambda **kw: None, {"qwen": bundle}) == {
        "qwen": target
    }


@pytest_asyncio.fixture
async def service():
    from kollabor_voice.audio import SpeechSegmenter
    from test_device_service import Models, Recorder, Speaker

    with tempfile.TemporaryDirectory(prefix="kv-", dir="/tmp") as directory:
        root = Path(directory)
        server = VoiceService(
            root,
            load_assets=lambda *args: {},
            load_models=lambda _: Models(),
            recorder_factory=Recorder,
            segmenter_factory=lambda: SpeechSegmenter(lambda _: True),
            player=Speaker(),
        )
        await server.start()
        try:
            yield server, root
        finally:
            await server.close()


@pytest.mark.asyncio
async def test_service_loads_one_local_classifier_and_routes_to_it(service):
    server, root = service
    calls = []
    for worker in server.classifiers.values():

        def prepare(worker=worker):
            calls.append(("prepare", worker.name))
            worker.state = {"state": "ready", "model": worker.model}

        async def close(worker=worker):
            calls.append(("close", worker.name))
            worker.state = {"state": "off", "model": worker.model}

        worker.prepare, worker.close = prepare, close

    async def decide(records, context, transcript_context, context_lines):
        return VoiceDecision(
            "respond", [r["event_id"] for r in records], "qwen"
        ).to_wire()

    async def answer(held, question, text):
        return {
            "answer": "yes",
            "probabilities": {"yes": 0.9, "no": 0.05, "other": 0.05},
        }

    server.classifiers["qwen"].decide = decide
    server.classifiers["qwen"].answer = answer
    client = VoiceClient(root)
    lease = await client.call(
        "claim", client_id="one", claim_id="one", activated_at=time.time()
    )
    auth = {"token": lease["token"], "epoch": lease["epoch"]}

    # Default: Laya, exactly as before.
    status = await client.call("classifier_prepare")
    assert status["name"] == "laya" and calls == [("prepare", "laya")]
    with pytest.raises(VoiceError, match="classifier changed"):
        await client.call("classifier_submit", **auth, model="qwen", records=[])
    with pytest.raises(VoiceError, match="does not answer"):
        await client.call(
            "classifier_submit", **auth, task="answer", held="a", question="b", text="c"
        )

    status = await client.call("classifier_prepare", model="qwen")
    assert status["name"] == "qwen" and status["state"] == "ready"
    assert calls[1:] == [("close", "laya"), ("prepare", "qwen")]
    assert "classifier" not in await client.call("status")  # voice status stays classifier-free

    local = LocalClassifier(client, lambda: auth, "qwen")
    reply = await local.answer("Check the build.", "Sorry, was that for me?", "Yes.")
    assert reply["answer"] == "yes"
    from kollabor_voice.classifiers import DecisionRequest

    decision = await local.decide(DecisionRequest([{"event_id": "a", "text": "Hi."}]))
    assert decision.provider == "qwen" and decision.event_ids == ["a"]
    with pytest.raises(VoiceError, match="unknown|Choose laya or qwen"):
        await client.call("classifier_prepare", model="provider")


@pytest.mark.asyncio
async def test_worker_process_reports_why_it_did_not_start(tmp_path, monkeypatch):
    """The real worker module, run without MLX: a clear error, not a hang."""
    import kollabor_voice.assets as assets
    import kollabor_voice.bootstrap as bootstrap
    import kollabor_voice.qwen as qwen

    monkeypatch.setattr(qwen, "apple_silicon", lambda: True)
    monkeypatch.setattr(
        bootstrap, "ensure_runtime", lambda *args, **kw: Path(sys.executable)
    )
    monkeypatch.setattr(
        assets, "ensure_models", lambda root, progress, bundles: {"qwen": tmp_path}
    )
    worker = QwenWorker(tmp_path)
    if "mlx" in sys.modules or _importable("mlx_lm"):
        pytest.skip("MLX is installed here; this checks a runtime without it")
    worker.prepare()
    await worker.setup_task
    status = worker.status()
    assert status["state"] == "error"
    assert "Fine-Tuned Qwen3 0.6B setup failed" in status["detail"]
    assert "mlx" in status["detail"].lower()
    await worker.close()


def _importable(name):
    import importlib.util

    return importlib.util.find_spec(name) is not None
