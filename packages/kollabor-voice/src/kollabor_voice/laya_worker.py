"""Persistent local Laya worker. One load, JSONL on stdio, no provider credentials."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path

from .classifiers import LAYA_QUESTION, VoiceDecision
from .control import manifest
from .observer import playback_text, utterance_groups
from .speech_review import SPEECH_QUESTION, SpeechDecision, format_decision


def predict_speech(agent, text):
    fixed = format_decision(text)
    if fixed:
        return fixed
    answer = agent.predict({"text": text}, SPEECH_QUESTION)["answers"]["spoken_style"]
    choice = answer["choice"]
    if choice not in {"speak", "rewrite"}:
        raise ValueError("Invalid speech-style decision")
    confidence = answer["probabilities"][choice]
    if confidence < 0.8:
        return SpeechDecision(
            "defer",
            "Laya is unsure whether this is suitable spoken language",
            "laya",
            "spoken-style-v1",
            confidence,
        )
    if choice == "speak":
        return SpeechDecision(
            "speak", provider="laya", model="spoken-style-v1", confidence=confidence
        )
    return SpeechDecision(
        "rewrite",
        "Use brief conversational language suitable for speech",
        "laya",
        "spoken-style-v1",
        confidence,
    )


def _intent_choice(agent, state):
    import math

    answer = agent.predict(state, LAYA_QUESTION)["answers"]["response"]
    choice, probs = answer["choice"], answer["probabilities"]
    if choice not in {"respond", "ignore"} or set(probs) != {"respond", "ignore"}:
        raise ValueError("Laya returned an invalid choice")
    if (
        any(
            isinstance(p, bool)
            or not isinstance(p, (int, float))
            or not math.isfinite(p)
            or not 0 <= p <= 1
            for p in probs.values()
        )
        or abs(sum(probs.values()) - 1) > 0.01
    ):
        raise ValueError("Laya returned invalid probabilities")
    return choice, probs[choice]


def predict(agent, records, context, transcript_context=None, context_lines=10):
    """One decision per connected utterance; never silently truncate its words."""
    from .classifiers import MAX_CONTEXT_LINES

    if type(context_lines) is not int or not 1 <= context_lines <= MAX_CONTEXT_LINES:
        raise ValueError("Invalid transcript context window")
    transcript_context = transcript_context or []
    if len(transcript_context) > context_lines:
        raise ValueError("Transcript context exceeds its configured window")
    previous = list(transcript_context)
    groups = utterance_groups(records)
    selected, probabilities, deferred, reasons, trace = [], [], [], [], []
    context_conflict = False
    minimum = manifest()["classifiers"]["laya"]["minimum_probability"]
    for group in groups:
        recent = previous[-context_lines:]
        previous.extend(group)
        if any(r.get("language") not in {None, "en", "english"} for r in group):
            deferred.extend(r["event_id"] for r in group)
            reasons.append("Laya's voice classifier is trained for English")
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
        # Laya reserves head tokens for the typed question. Reject oversized
        # state instead of losing the beginning/end to tokenizer truncation.
        budget = agent.cfg["max_len"] - agent.cfg["head_max_len"] - 32
        # Conversation context is optional. It must never displace the user's
        # current words or turn a short request into an oversized utterance.
        while (
            state["recent_conversation"]
            and len(agent.tok.encode(json.dumps(state, ensure_ascii=False))) > budget
        ):
            state["recent_conversation"] = state["recent_conversation"][
                : len(state["recent_conversation"]) // 2
            ]
        if len(agent.tok.encode(json.dumps(state, ensure_ascii=False))) > budget:
            deferred.extend(r["event_id"] for r in group)
            reasons.append("Speech and transcript history exceed Laya's context")
            continue
        passes = [_intent_choice(agent, state)]
        if recent or (passes[0][1] < minimum and state["recent_conversation"]):
            # The small head can over-weight history and suppress a clear new
            # request ("Hello, can you hear me?" is 0.71 with history, 0.99
            # alone), so the words are judged on their own too. A pass at or
            # above the threshold decides; the agent is asked only when no pass
            # is sure, or the sure ones disagree.
            alone = {k: v for k, v in state.items() if k != "recent_transcripts"}
            alone["recent_conversation"] = ""
            passes.append(_intent_choice(agent, alone))
        trace.append(
            {
                "state": state,
                "passes": [
                    {"label": label, "choice": choice, "probability": round(p, 3)}
                    for label, (choice, p) in zip(("with context", "words alone"), passes)
                ],
            }
        )
        sure = [p for p in passes if p[1] >= minimum]
        if not sure or len({c for c, _ in sure}) > 1:
            probabilities.extend(p for _, p in passes)
            deferred.extend(r["event_id"] for r in group)
            context_conflict |= bool(sure)
            reasons.append(
                "Laya needs the agent to resolve transcript context"
                if sure
                else "Laya is uncertain"
            )
            continue
        probabilities.extend(p for _, p in sure)
        if sure[0][0] == "respond":
            selected.extend(r["event_id"] for r in group)
    return VoiceDecision(
        "respond" if selected else "defer" if deferred else "ignore",
        [r["event_id"] for r in records if r["event_id"] in set(selected + deferred)],
        "laya",
        "voice-intent-v1",
        min(probabilities) if probabilities and not context_conflict else None,
        (
            "; ".join(dict.fromkeys(reasons))
            + ". Asking the agent to decide whether a response is needed."
            if deferred
            else ""
        ),
        deferred,
        [r["event_id"] for r in transcript_context],
        trace,
    )


def send(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    with contextlib.redirect_stdout(sys.stderr):
        import hashlib

        import laya

        from .assets import verified
        from .laya_voice_model import VoiceIntentModel

        head = manifest()["classifiers"]["laya"]["voice_head"]
        head_path = Path(__file__).with_name(head["name"])
        question_hash = hashlib.sha256(
            json.dumps(LAYA_QUESTION, sort_keys=True).encode()
        ).hexdigest()
        if not verified(head_path, head) or question_hash != head["question_sha256"]:
            raise RuntimeError(
                "Laya voice head integrity check failed; reinstall kollabor-voice"
            )
        agent = VoiceIntentModel(laya.load(str(args.model)), head_path)
        speech_head = manifest()["classifiers"]["laya"]["speech_head"]
        speech_path = Path(__file__).with_name(speech_head["name"])
        if (
            not verified(speech_path, speech_head)
            or hashlib.sha256(
                json.dumps(SPEECH_QUESTION, sort_keys=True).encode()
            ).hexdigest()
            != speech_head["question_sha256"]
        ):
            raise RuntimeError(
                "Laya speech head integrity check failed; reinstall kollabor-voice"
            )
        speech_agent = VoiceIntentModel(
            agent.agent, speech_path, SPEECH_QUESTION, speech_head
        )
        # The first forward pass also initializes the device. Pay it before ready.
        agent.predict(
            {"text": "Voice classifier warmup", "recent_conversation": ""},
            LAYA_QUESTION,
        )
        predict_speech(speech_agent, "Voice is ready.")
    send(
        {
            "ready": True,
            "model": "typed-decisions",
            "cold_seconds": time.monotonic() - started,
        }
    )
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                decision = (
                    predict_speech(speech_agent, request["text"])
                    if request.get("task") == "speech"
                    else predict(
                        agent,
                        request["records"],
                        request.get("context", ""),
                        request.get("transcript_context", []),
                        request.get("context_lines", 10),
                    )
                )
            send({"id": request["id"], "result": decision.to_wire()})
        except Exception as exc:
            send({"id": request.get("id"), "error": str(exc)})


if __name__ == "__main__":
    main()
