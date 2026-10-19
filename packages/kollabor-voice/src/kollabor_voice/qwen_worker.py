"""Persistent fine-tuned Qwen3-0.6B voice worker on MLX. One load, JSON lines on stdio.

One LoRA-tuned model makes the three voice decisions, each as one constrained
token: intent (respond | ignore), the reply to "was that for me?" (yes | no |
other) and spoken style (say | rewrite). These are the prompts it was trained
on; bench/qwen_voice.py imports them from here, and manifest.json pins their
hash, so changing them needs new weights.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
import time
from pathlib import Path

from .classifiers import LAYA_QUESTION, MAX_CONTEXT_LINES, VoiceDecision
from .control import manifest
from .observer import playback_text, utterance_groups
from .speech_review import SPEECH_QUESTION, SpeechDecision, format_decision

LABELS = {
    "intent": ("respond", "ignore"),
    "answer": ("yes", "no", "other"),
    "style": ("say", "rewrite"),
}


def system_prompts():
    intent = LAYA_QUESTION["response"]
    style = SPEECH_QUESTION["spoken_style"]
    return {
        "intent": (
            f"{intent['instructions']} The microphone also picks up TV, videos, other "
            "people in the room and the assistant's own voice.\n"
            f"respond: {intent['criteria']['respond']}\n"
            f"ignore: {intent['criteria']['ignore']}\n"
            "Answer with one word: respond or ignore."
        ),
        "answer": (
            "The voice assistant was unsure whether some speech was meant for it, so "
            "it asked the user. Decide what the user's reply means.\n"
            "yes: the speech was for the assistant.\n"
            "no: it was not (the TV, someone else, ignore it).\n"
            "other: the reply is not an answer to the question (background speech, "
            "a new request, a filler).\n"
            "Answer with one word: yes, no or other."
        ),
        "style": (
            f"{style['instructions']}\n"
            f"say: {style['criteria']['speak']}\n"
            f"rewrite: {style['criteria']['rewrite']}\n"
            "Answer with one word: say or rewrite."
        ),
    }


def prompt_sha256():
    contract = {"system": system_prompts(), "labels": LABELS}
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()


def messages(case, system):
    if case["task"] == "intent":
        parts = []
        if case.get("context"):
            parts.append("Recent chat:\n" + case["context"])
        if case.get("transcript_context"):
            lines = "\n".join(f"- {t[:200]}" for t in case["transcript_context"])
            parts.append("Earlier microphone lines, oldest first:\n" + lines)
        if case.get("assistant_playback"):
            spoken = " ".join(f'"{t}"' for t in case["assistant_playback"])
            parts.append("The assistant was speaking at the time: " + spoken)
        parts.append(f'Heard now: "{case["text"]}"')
        user = "\n\n".join(parts)
    elif case["task"] == "answer":
        user = (
            f'The speech it asked about: "{case["held"]}"\n'
            f'The assistant asked: "{case["question"]}"\n'
            f'The reply: "{case["text"]}"'
        )
    else:
        user = case["text"]
    return [
        {"role": "system", "content": system[case["task"]]},
        {"role": "user", "content": user},
    ]


def prompt_text(tokenizer, case, system):
    return tokenizer.apply_chat_template(
        messages(case, system),
        add_generation_prompt=True,
        enable_thinking=False,
        tokenize=False,
    )


class QwenVoiceModel:
    """Fused MLX 8-bit weights: one forward pass, softmax over the task's labels."""

    def __init__(self, path):
        import mlx.core as mx
        from mlx_lm import load

        # Uncapped, MLX's buffer cache grew to a 15 GB footprint (bench/README.md).
        mx.set_cache_limit(256 * 2**20)
        self.mx = mx
        self.model, self.tokenizer = load(str(path))
        self.system = system_prompts()
        self.label_ids = {}
        for task, labels in LABELS.items():
            ids = [self.tokenizer.encode(x, add_special_tokens=False) for x in labels]
            if any(len(i) != 1 for i in ids):
                raise RuntimeError("Each voice label must be one token")
            self.label_ids[task] = mx.array([i[0] for i in ids])

    def probabilities(self, case):
        mx = self.mx
        tokens = self.tokenizer.encode(
            prompt_text(self.tokenizer, case, self.system), add_special_tokens=False
        )
        hidden = self.model.model(mx.array(tokens)[None])[:, -1]
        logits = self.model.model.embed_tokens.as_linear(hidden)[0]
        probs = mx.softmax(logits[self.label_ids[case["task"]]].astype(mx.float32))
        return dict(zip(LABELS[case["task"]], probs.tolist()))


def _spec():
    return manifest()["classifiers"]["qwen"]


def predict(model, records, context, transcript_context=None, context_lines=10):
    """One decision per connected utterance from a single pass, as measured."""
    if type(context_lines) is not int or not 1 <= context_lines <= MAX_CONTEXT_LINES:
        raise ValueError("Invalid transcript context window")
    transcript_context = transcript_context or []
    if len(transcript_context) > context_lines:
        raise ValueError("Transcript context exceeds its configured window")
    spec = _spec()
    previous = list(transcript_context)
    selected, deferred, probabilities, reasons, trace = [], [], [], [], []
    for group in utterance_groups(records):
        recent = previous[-context_lines:]
        previous.extend(group)
        ids = [r["event_id"] for r in group]
        if any(r.get("language") not in {None, "en", "english"} for r in group):
            deferred.extend(ids)
            reasons.append("The Qwen voice model is trained for English")
            continue
        state = {
            "text": " ".join(r["text"] for r in group),
            "recent_conversation": context[-2000:],
        }
        if recent:
            state["recent_transcripts"] = [r["text"] for r in recent]
        playback = playback_text(group)
        if playback:
            state["assistant_playback"] = playback
        probs = model.probabilities(
            {
                "task": "intent",
                "text": state["text"],
                "context": state["recent_conversation"],
                "transcript_context": state.get("recent_transcripts", []),
                "assistant_playback": playback,
            }
        )
        choice = max(probs, key=probs.get)
        probabilities.append(probs[choice])
        trace.append(
            {
                "state": state,
                "passes": [
                    {
                        "label": "with context",
                        "choice": choice,
                        "probability": round(probs[choice], 3),
                    }
                ],
            }
        )
        if probs[choice] < spec["minimum_probability"]:
            deferred.extend(ids)
            reasons.append("Qwen is uncertain")
        elif choice == "respond":
            selected.extend(ids)
    return VoiceDecision(
        "respond" if selected else "defer" if deferred else "ignore",
        [r["event_id"] for r in records if r["event_id"] in set(selected + deferred)],
        "qwen",
        spec["model"],
        min(probabilities) if probabilities else None,
        (
            "; ".join(dict.fromkeys(reasons))
            + ". Asking the human whether it was for the agent."
            if deferred
            else ""
        ),
        deferred,
        [r["event_id"] for r in transcript_context],
        trace,
    )


def predict_answer(model, held, question, text):
    """What the reply to "was that for me?" means; the argmax, never unsure."""
    probs = model.probabilities(
        {"task": "answer", "held": held, "question": question, "text": text}
    )
    return {
        "answer": max(probs, key=probs.get),
        "probabilities": probs,
        "provider": "qwen",
        "model": _spec()["model"],
    }


def predict_speech(model, text):
    fixed = format_decision(text)
    if fixed:
        return fixed
    spec = _spec()
    probs = model.probabilities({"task": "style", "text": text})
    choice = max(probs, key=probs.get)
    confidence = probs[choice]
    if confidence < spec["minimum_probability"]:
        return SpeechDecision(
            "defer",
            "Qwen is unsure whether this is suitable spoken language",
            "qwen",
            spec["model"],
            confidence,
        )
    if choice == "say":
        return SpeechDecision(
            "speak", provider="qwen", model=spec["model"], confidence=confidence
        )
    return SpeechDecision(
        "rewrite",
        "Use brief conversational language suitable for speech",
        "qwen",
        spec["model"],
        confidence,
    )


def send(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    try:
        # Library chatter goes to the log, never into the protocol.
        with contextlib.redirect_stdout(sys.stderr):
            if prompt_sha256() != _spec()["prompt_sha256"]:
                raise RuntimeError(
                    "Qwen voice prompts differ from the ones its weights were "
                    "trained on; reinstall kollabor-voice"
                )
            model = QwenVoiceModel(args.model)
            # The first passes build the Metal kernels. Pay that before ready.
            predict_speech(model, "Voice is ready.")
            predict(
                model,
                [{"event_id": "warmup", "text": "Voice classifier warmup"}],
                "",
            )
    except Exception as exc:
        send({"ready": False, "error": f"{type(exc).__name__}: {exc}"})
        return
    send(
        {
            "ready": True,
            "model": _spec()["model"],
            "cold_seconds": time.monotonic() - started,
            "device": str(model.mx.default_device()),
        }
    )
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            task = request.get("task")
            with contextlib.redirect_stdout(sys.stderr):
                if task == "speech":
                    result = predict_speech(model, request["text"]).to_wire()
                elif task == "answer":
                    result = predict_answer(
                        model, request["held"], request["question"], request["text"]
                    )
                else:
                    result = predict(
                        model,
                        request["records"],
                        request.get("context", ""),
                        request.get("transcript_context", []),
                        request.get("context_lines", 10),
                    ).to_wire()
            send({"id": request["id"], "result": result})
        except Exception as exc:
            send({"id": request.get("id"), "error": str(exc)})


if __name__ == "__main__":
    main()
