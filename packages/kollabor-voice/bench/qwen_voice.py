"""Fine-tune Qwen3-0.6B on kollab's voice decisions and score it next to Laya.

One LoRA adapter covers three tasks; each answer is a single constrained token:
  intent  is this microphone speech for the agent?          respond | ignore
  answer  the reply to the classifier's "was that for me?"   yes | no | other
  style   can this reply be said aloud as written?           say | rewrite

Synthetic cases come from this folder. Real household speech is read from
~/.kollab/voice/training (real-intent.jsonl, real-answers.jsonl,
real-style.jsonl); it is private and this script never copies it elsewhere.

  train      LoRA on the train splits (MLX), epoch picked on calibration;
             --playback adds generated echo/talk-over cases, --real the private split
  score      per-case probabilities and latency: mlx, llama, torch or laya;
             runtime runs the shipped worker's own code (kollabor_voice.qwen_worker)
  laya-head  ablation: refit the Laya RBF intent head on the same data
  report     accuracy and runtime-policy counts from score files

Splits named train/calibration fit and select; every other split is evaluation.
"""

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH.parent / "src"))
# The prompts the shipped worker sends: training and scoring use the same ones.
from kollabor_voice.qwen_worker import LABELS, prompt_text  # noqa: E402
from kollabor_voice.qwen_worker import system_prompts as _system  # noqa: E402

PRIVATE = Path.home() / ".kollab/voice/training"
BASE = "Qwen/Qwen3-0.6B"
BASE_REVISION = "c1899de289a04d12100db370d81485cdf75e47ca"
THRESHOLD = 0.8  # manifest.json minimum_probability, the Laya policy guard


def _fixture(name):
    return json.loads((BENCH / name).read_text())["cases"]


def _private(name):
    path = PRIVATE / name
    return [json.loads(line) for line in path.open()] if path.exists() else []


INTERJECTIONS = (
    "Wait, stop.", "Hold on, which file?", "No, the other branch.", "Can you repeat that?",
    "Stop talking for a second.", "Actually, check the logs first.", "Skip that part.",
    "Sorry, what was the last thing?", "Okay, and then what?", "Go back one step.",
    "Pause.", "That's not what I asked.", "Make it shorter please.", "Which one failed?",
)
ROOM = (
    "And now back to the studio.", "The quarterback takes the snap.",
    "Mom, where's my backpack?", "Coming up after the break.", "I'll be there in five.",
    "This episode is sponsored by", "Did anyone let the dog out?", "Two more minutes!",
)
MISHEARD = {"the": "a", "is": "was", "check": "chuck", "tests": "test", "file": "while",
            "I'll": "I", "now": "no", "run": "ran", "voice": "boys", "branch": "brunch"}


def playback_cases():
    """Echo and talk-over cases built from synthetic assistant lines (no real speech).

    The microphone hears the agent's own reply: a fragment or slightly misheard copy
    of what is playing is echo (ignore); different words over it are the human
    (respond), unless they are the room (ignore).
    """
    rng = random.Random(7)
    lines = [c["text"] for c in _fixture("spoken-style.json")
             if c["split"] == "train" and c["expected"] == "speak"]
    out = []
    for i, line in enumerate(lines):
        words = line.split()
        split = "calibration" if i % 6 == 0 else "train"
        start = rng.randrange(0, max(1, len(words) - 2))
        fragment = " ".join(words[start : start + rng.randint(2, 6)])
        misheard = " ".join(MISHEARD.get(w, w) if rng.random() < 0.3 else w for w in words)
        tail = " ".join(words[-rng.randint(2, 4):])
        variants = [
            (fragment, "ignore"),
            (misheard, "ignore"),
            (rng.choice(INTERJECTIONS), "respond"),
            (f"{tail} {rng.choice(INTERJECTIONS)}", "respond"),
            (rng.choice(ROOM), "ignore"),
        ]
        for j, (text, expected) in enumerate(variants):
            out.append({"task": "intent", "source": "synthetic-playback", "split": split,
                        "id": f"pb{i}-{j}", "text": text, "context": "",
                        "assistant_playback": [line], "expected": expected})
    return out


def load_cases(playback=False):
    """Every labelled case as {task, source, split, id, expected, ...fields}."""
    out = playback_cases() if playback else []
    for i, c in enumerate(_fixture("voice-intent.json")):
        out.append({**c, "task": "intent", "source": "synthetic", "id": c.get("id", f"vi{i}")})
    for i, c in enumerate(_fixture("voice-acceptance.json")):
        out.append({**c, "task": "intent", "source": "synthetic", "split": "acceptance", "id": f"va{i}"})
    for i, c in enumerate(_fixture("voice-playback-regressions.json")):
        out.append({**c, "task": "intent", "source": "synthetic", "split": "playback", "id": f"vp{i}"})
    for c in _private("real-intent.jsonl"):
        if c["expected"] == "unclear" or c["language"] not in {None, "en", "english"}:
            continue
        split = c["split"]
        if split == "train" and int(c["gid"][1:]) % 7 == 0:
            split = "calibration"
        out.append({
            "task": "intent", "source": "real", "split": "real-test" if split == "test" else split,
            "id": c["gid"], "expected": c["expected"], "text": c["text"], "context": c["context"],
            "transcript_context": [r["text"] for r in c["transcript_context"]],
            "assistant_playback": c["assistant_playback"], "records": c["records"],
            "transcript_records": c["transcript_context"], "said": c["source"] == "said",
        })
    for i, c in enumerate(_fixture("voice-answers.json")):
        out.append({**c, "task": "answer", "source": "synthetic", "id": f"an{i}"})
    for c in _private("real-answers.jsonl"):
        if c["expected"] != "unclear":
            out.append({**c, "task": "answer", "source": "real", "split": "real-test", "id": c["gid"]})
    for i, c in enumerate(_fixture("spoken-style.json")):
        out.append({**c, "task": "style", "source": "synthetic", "id": f"ss{i}",
                    "expected": "say" if c["expected"] == "speak" else c["expected"]})
    for c in _private("real-style.jsonl"):
        if c["expected"] != "unclear":
            out.append({**c, "task": "style", "source": "real", "split": "real-test",
                        "expected": "say" if c["expected"] == "speak" else c["expected"]})
    return out


# --------------------------------------------------------------------- train


def train(args):
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    from mlx.utils import tree_flatten
    from mlx_lm import load
    from mlx_lm.tuner.utils import linear_to_lora_layers

    random.seed(args.seed)
    mx.random.seed(args.seed)
    system = _system()
    model, tok = load(BASE, revision=BASE_REVISION)
    label_id = {
        label: tok.encode(label, add_special_tokens=False)
        for labels in LABELS.values() for label in labels
    }
    assert all(len(ids) == 1 for ids in label_id.values()), label_id

    def encode(case):
        ids = tok.encode(prompt_text(tok, case, system), add_special_tokens=False)
        return ids, label_id[case["expected"]][0], case

    usable = [
        c for c in load_cases(playback=args.playback)
        if c["split"] in {"train", "calibration"} and (args.real or c["source"] != "real")
    ]
    fit = [encode(c) for c in usable if c["split"] == "train"]
    select = sorted(
        (encode(c) for c in usable if c["split"] == "calibration"), key=lambda it: len(it[0])
    )
    print(f"train {len(fit)} calibration {len(select)}", flush=True)

    model.freeze()
    lora = {"rank": args.rank, "scale": args.scale, "dropout": args.dropout}
    linear_to_lora_layers(model, args.layers, lora)
    pad = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id

    def batch(items):
        width = max(len(ids) for ids, _, _ in items)
        x = mx.array([ids + [pad] * (width - len(ids)) for ids, _, _ in items])
        last = mx.array([len(ids) - 1 for ids, _, _ in items])
        y = mx.array([label for _, label, _ in items])
        return x, last, y

    def logits_at(model, x, last):
        # Right padding: causal attention keeps every real position unaffected.
        h = model.model(x)[mx.arange(x.shape[0]), last]
        return model.model.embed_tokens.as_linear(h).astype(mx.float32)

    def loss_fn(model, x, last, y):
        return nn.losses.cross_entropy(logits_at(model, x, last), y).mean()

    loss_and_grad = nn.value_and_grad(model, loss_fn)
    steps = args.epochs * ((len(fit) + args.batch - 1) // args.batch)
    schedule = optim.join_schedules(
        [optim.linear_schedule(0, args.lr, max(1, steps // 20)), optim.cosine_decay(args.lr, steps)],
        [max(1, steps // 20)],
    )
    opt = optim.AdamW(learning_rate=schedule, weight_decay=0.0)

    def evaluate():
        model.eval()
        total, correct = 0.0, 0
        for i in range(0, len(select), args.batch):
            x, last, y = batch(select[i : i + args.batch])
            logits = logits_at(model, x, last)
            total += nn.losses.cross_entropy(logits, y, reduction="sum").item()
            correct += (mx.argmax(logits, axis=-1) == y).sum().item()
        model.train()
        return total / max(1, len(select)), correct

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    best = None
    started = time.monotonic()
    model.train()
    for epoch in range(1, args.epochs + 1):
        # Mixed-task random batches. Length-sorted batches trained faster but
        # grouped the tasks and fit worse (answers 43/60 vs 55/60 on heldout).
        random.shuffle(fit)
        batches = [fit[i : i + args.batch] for i in range(0, len(fit), args.batch)]
        losses = []
        for items in batches:
            x, last, y = batch(items)
            loss, grads = loss_and_grad(model, x, last, y)
            opt.update(model, grads)
            mx.eval(model.parameters(), opt.state, loss)
            losses.append(loss.item())
        cal_loss, cal_correct = evaluate()
        print(
            f"epoch {epoch} train_loss {statistics.mean(losses):.4f} "
            f"calibration_loss {cal_loss:.4f} calibration_correct {cal_correct}/{len(select)} "
            f"{time.monotonic() - started:.0f}s",
            flush=True,
        )
        if best is None or cal_loss < best[0]:
            best = (cal_loss, epoch)
            weights = dict(tree_flatten(model.trainable_parameters()))
            mx.save_safetensors(str(out / "adapters.safetensors"), weights)
    (out / "adapter_config.json").write_text(json.dumps({
        "fine_tune_type": "lora", "num_layers": args.layers, "lora_parameters": lora,
        "base": BASE, "base_revision": BASE_REVISION, "real_data": args.real,
        "playback_augmentation": args.playback,
        "epochs": args.epochs, "best_epoch": best[1], "calibration_loss": best[0],
        "lr": args.lr, "batch": args.batch, "seed": args.seed,
        "train_count": len(fit), "calibration_count": len(select),
        "train_seconds": time.monotonic() - started,
    }, indent=2) + "\n")
    print(f"best epoch {best[1]} calibration_loss {best[0]:.4f} -> {out}")


# --------------------------------------------------------------------- score


def _eval_cases(args):
    return [
        c for c in load_cases()
        if c["split"] not in {"train", "calibration"} and (not args.task or c["task"] in args.task)
    ]


def _write(args, rows, meta):
    Path(args.out).write_text(
        json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False) + "\n"
    )
    print(f"{len(rows)} cases -> {args.out}")


def _row(case, probs, ms, decision=None):
    keep = ("task", "source", "split", "id", "expected", "said")
    return {
        **{k: case[k] for k in keep if k in case},
        "probs": probs, "ms": ms, "decision": decision,
    }


def score_mlx(args):
    import mlx.core as mx
    from mlx_lm import load

    system = _system()
    # Uncapped, MLX's buffer cache grew to a 15 GB footprint over 462 prompts of varying length.
    mx.set_cache_limit(256 * 1024 * 1024)
    started = time.monotonic()
    model, tok = load(args.model)
    load_s = time.monotonic() - started
    ids = {t: [tok.encode(lb, add_special_tokens=False)[0] for lb in ls] for t, ls in LABELS.items()}
    rows = []
    for case in _eval_cases(args):
        t0 = time.perf_counter()
        x = mx.array(tok.encode(prompt_text(tok, case, system), add_special_tokens=False))[None]
        logits = model.model.embed_tokens.as_linear(model.model(x)[:, -1])[0]
        probs = mx.softmax(logits[mx.array(ids[case["task"]])].astype(mx.float32))
        probs = probs.tolist()
        ms = 1000 * (time.perf_counter() - t0)
        rows.append(_row(case, dict(zip(LABELS[case["task"]], probs)), ms))
    _write(args, rows, {"backend": "mlx", "model": str(args.model), "load_seconds": load_s,
                        "peak_memory_bytes": mx.get_peak_memory()})


def _bench_records(case):
    """The case as transcript records, the way the voice service hands them over."""
    return case.get("records") or [{
        "event_id": case["id"], "owner_epoch": "benchmark", "text": case["text"],
        "language": "en", "started_at": "2026-09-26T00:00:00+00:00",
        "ended_at": "2026-09-26T00:00:01+00:00",
        "playback_overlap": [{"reply_id": "benchmark", "text": t}
                             for t in case.get("assistant_playback", [])],
    }]


def score_runtime(args):
    """The shipped worker's code path: records in, the runtime's decisions out."""
    from kollabor_voice.qwen_worker import (
        QwenVoiceModel,
        predict,
        predict_answer,
        predict_speech,
    )

    started = time.monotonic()
    model = QwenVoiceModel(args.model)
    load_s = time.monotonic() - started
    rows = []
    for case in _eval_cases(args):
        t0 = time.perf_counter()
        if case["task"] == "intent":
            decision = predict(model, _bench_records(case), case.get("context", ""),
                               case.get("transcript_records") or [], 10)
            probs = None  # a language the model is not for: deferred unscored
            if decision.trace:
                first = decision.trace[0]["passes"][0]
                other = "ignore" if first["choice"] == "respond" else "respond"
                # The trace rounds to 3 places; the decision used the raw value.
                probs = {first["choice"]: first["probability"],
                         other: 1 - first["probability"]}
            verdict = decision.decision
        elif case["task"] == "answer":
            result = predict_answer(model, case["held"], case["question"], case["text"])
            probs, verdict = result["probabilities"], result["answer"]
        else:
            result = predict_speech(model, case["text"])
            verdict = {"speak": "say", "rewrite": "rewrite", "defer": "defer",
                       "silent": "say"}[result.decision]
        ms = 1000 * (time.perf_counter() - t0)
        if case["task"] == "style":
            # The model's raw split for argmax, untimed; the timed call is the policy.
            probs = model.probabilities({"task": "style", "text": case["text"]})
        rows.append(_row(case, probs, ms, verdict))
    _write(args, rows, {"backend": "runtime", "model": str(args.model), "load_seconds": load_s,
                        "peak_memory_bytes": model.mx.get_peak_memory()})


def score_llama(args):
    import numpy as np
    from llama_cpp import Llama
    from transformers import AutoTokenizer

    system = _system()
    tok = AutoTokenizer.from_pretrained(BASE, revision=BASE_REVISION)
    started = time.monotonic()
    # The longest prompt is ~830 tokens; 2048 keeps the KV cache to ~235 MB.
    llm = Llama(model_path=str(args.model), n_ctx=2048, n_batch=2048, n_gpu_layers=-1,
                verbose=False)
    load_s = time.monotonic() - started
    ids = {
        t: [llm.tokenize(lb.encode(), add_bos=False, special=False)[0] for lb in ls]
        for t, ls in LABELS.items()
    }
    rows = []
    for case in _eval_cases(args):
        t0 = time.perf_counter()
        tokens = llm.tokenize(prompt_text(tok, case, system).encode(), add_bos=False, special=True)
        llm.reset()
        llm.eval(tokens)
        last = np.ctypeslib.as_array(llm._ctx.get_logits_ith(-1), shape=(llm.n_vocab(),))
        logits = last[ids[case["task"]]].astype(np.float64)
        probs = np.exp(logits - logits.max())
        probs /= probs.sum()
        ms = 1000 * (time.perf_counter() - t0)
        rows.append(_row(case, dict(zip(LABELS[case["task"]], probs.tolist())), ms))
    _write(args, rows, {"backend": "llama.cpp", "model": str(args.model), "load_seconds": load_s})


def score_torch(args):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    system = _system()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    started = time.monotonic()
    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.float16).to(device).eval()
    load_s = time.monotonic() - started
    ids = {t: [tok.encode(lb, add_special_tokens=False)[0] for lb in ls] for t, ls in LABELS.items()}
    rows = []
    for case in _eval_cases(args):
        t0 = time.perf_counter()
        x = torch.tensor([tok.encode(prompt_text(tok, case, system), add_special_tokens=False)], device=device)
        with torch.inference_mode():
            logits = model(x, logits_to_keep=1).logits[0, -1, ids[case["task"]]].float()
            probs = torch.softmax(logits, -1).cpu().tolist()
        ms = 1000 * (time.perf_counter() - t0)
        rows.append(_row(case, dict(zip(LABELS[case["task"]], probs)), ms))
    _write(args, rows, {"backend": f"torch-{device}", "model": str(args.model), "load_seconds": load_s})


def score_laya(args):
    """The shipped policy: Laya intent head (two passes), regex answers, style head."""
    import kollabor_voice
    import laya
    from kollabor_voice import acks
    from kollabor_voice.control import manifest
    from kollabor_voice.laya_voice_model import VoiceIntentModel
    from kollabor_voice.laya_worker import predict, predict_speech
    from kollabor_voice.speech_review import SPEECH_QUESTION

    started = time.monotonic()
    spec = manifest()["classifiers"]["laya"]
    root = Path(kollabor_voice.__file__).parent
    head, head_spec = root / spec["voice_head"]["name"], None
    if args.head:  # an ablation head from `laya-head`, with its own kernel gamma
        head = Path(args.head)
        head_spec = json.loads(head.with_suffix(".json").read_text())
    intent = VoiceIntentModel(laya.load(str(args.model)), head, spec=head_spec)
    style = VoiceIntentModel(intent.agent, root / spec["speech_head"]["name"], SPEECH_QUESTION, spec["speech_head"])
    load_s = time.monotonic() - started
    intent.predict({"text": "Voice classifier warmup", "recent_conversation": ""},
                   intent.questions)
    rows = []
    for case in _eval_cases(args):
        t0 = time.perf_counter()
        if case["task"] == "intent":
            records = case.get("records") or [{
                "event_id": case["id"], "owner_epoch": "benchmark", "text": case["text"],
                "language": "en", "started_at": "2026-09-26T00:00:00+00:00",
                "ended_at": "2026-09-26T00:00:01+00:00",
                "playback_overlap": [{"reply_id": "benchmark", "text": t}
                                     for t in case.get("assistant_playback", [])],
            }]
            decision = predict(intent, records, case.get("context", ""),
                               case.get("transcript_records") or [], 10)
            first = decision.trace[0]["passes"][0] if decision.trace else None
            p = first["probability"] if first else None
            probs = (
                {first["choice"]: p, ("ignore" if first["choice"] == "respond" else "respond"): 1 - p}
                if first else None
            )
            verdict = decision.decision
        elif case["task"] == "answer":
            probs, verdict = None, acks.answer(case["text"]) or "other"
        else:
            result = predict_speech(style, case["text"])
            verdict = {"speak": "say", "rewrite": "rewrite", "defer": "defer",
                       "silent": "say"}[result.decision]
            probs = None
        ms = 1000 * (time.perf_counter() - t0)
        if case["task"] == "style" and result.provider == "laya":
            # The head's raw split for argmax; the timed call above is the policy.
            raw = style.predict({"text": case["text"]}, SPEECH_QUESTION)
            raw = raw["answers"]["spoken_style"]["probabilities"]
            probs = {"say": raw["speak"], "rewrite": raw["rewrite"]}
        rows.append(_row(case, probs, ms, verdict))
    _write(args, rows, {"backend": "laya", "model": str(args.model), "load_seconds": load_s})


def laya_head(args):
    """Ablation: refit the shipped RBF intent head recipe on the same train data.

    Mirrors train_voice_head.py (frozen encoder, mean-centred unit vectors, kernel
    grid chosen by calibration log loss) with the runtime's full state: recent
    transcripts and playback included, conversation trimmed to fit like predict().
    """
    import laya
    import numpy as np
    import torch
    from kollabor_voice.classifiers import LAYA_QUESTION
    from kollabor_voice.laya_voice_model import features
    from safetensors.torch import save_file

    agent = laya.load(str(args.model))
    budget = agent.cfg["max_len"] - agent.cfg["head_max_len"] - 32

    def state(case):
        s = {"text": case["text"], "recent_conversation": case.get("context", "")[-2000:]}
        if case.get("transcript_context"):
            s["recent_transcripts"] = case["transcript_context"][-10:]
        if case.get("assistant_playback"):
            s["assistant_playback"] = case["assistant_playback"]
        while s["recent_conversation"] and len(agent.tok.encode(json.dumps(s, ensure_ascii=False))) > budget:
            s["recent_conversation"] = s["recent_conversation"][len(s["recent_conversation"]) // 2 :]
        return s

    cases = [
        c for c in load_cases()
        if c["task"] == "intent" and c["split"] in {"train", "calibration"}
        and (args.real or c["source"] != "real")
    ]
    x = np.stack([features(agent, state(c), LAYA_QUESTION).cpu().numpy().astype(np.float64) for c in cases])
    train = np.array([c["split"] == "train" for c in cases])
    cal = ~train
    yt = np.array([1 if c["expected"] == "respond" else -1 for c in cases])[train]
    yc = np.array([c["expected"] == "respond" for c in cases])[cal]
    mean = x[train].mean(0)
    x = x - mean
    x /= np.linalg.norm(x, axis=1, keepdims=True).clip(min=1e-12)
    centres = x[train]
    distances = ((x[:, None, :] - centres[None, :, :]) ** 2).sum(-1)
    best = None
    for gamma in (0.25, 0.5, 1, 2, 4, 8, 16, 32, 64):
        kernel = np.exp(-gamma * distances)
        for reg in (0.001, 0.01, 0.1, 1, 10):
            alpha = np.linalg.solve(kernel[train] + reg * np.eye(train.sum()), yt)
            scores = kernel[cal] @ alpha
            for temp in (0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2):
                p = 1 / (1 + np.exp(-np.clip(scores / temp, -40, 40)))
                loss = float(-np.mean(yc * np.log(p + 1e-9) + (~yc) * np.log(1 - p + 1e-9)))
                if best is None or loss < best[0]:
                    best = (loss, gamma, reg, temp, alpha)
    loss, gamma, reg, temp, alpha = best
    save_file({
        "mean": torch.tensor(mean, dtype=torch.float32),
        "centres": torch.tensor(centres, dtype=torch.float32),
        "alpha": torch.tensor(alpha / temp, dtype=torch.float32),
    }, args.out)
    Path(args.out).with_suffix(".json").write_text(json.dumps({
        "kernel_gamma": gamma, "regularization": reg, "temperature": temp,
        "calibration_log_loss": loss, "real_data": args.real,
        "train_count": int(train.sum()), "calibration_count": int(cal.sum()),
    }, indent=2) + "\n")
    print(f"gamma {gamma} reg {reg} temp {temp} calibration_log_loss {loss:.4f} -> {args.out}")


# -------------------------------------------------------------------- report


def policy(row):
    """What the runtime does with this row: a label, or "defer" when unsure."""
    if row["decision"] is not None:
        return row["decision"]
    if row["task"] == "style":
        from kollabor_voice.speech_review import format_decision

        # Same order as the runtime: the format rule first, the model after.
        fixed = None
        if row.get("_text"):
            fixed = format_decision(row["_text"])
        if fixed is not None:
            return {"rewrite": "rewrite", "silent": "say"}[fixed.decision]
    label, p = max(row["probs"].items(), key=lambda kv: kv[1])
    if row["task"] == "answer":
        return label  # the regex never defers; neither does its replacement
    return label if p >= THRESHOLD else "defer"


def summarize(rows):
    out = {}
    for key in sorted({(r["task"], r["split"]) for r in rows}):
        sub = [r for r in rows if (r["task"], r["split"]) == key]
        decided = [policy(r) for r in sub]
        argmax = [max(r["probs"].items(), key=lambda kv: kv[1])[0] if r["probs"] else d
                  for r, d in zip(sub, decided)]
        auto = [(d, r["expected"]) for d, r in zip(decided, sub) if d != "defer"]
        ms = sorted(r["ms"] for r in sub)
        entry = {
            "count": len(sub),
            "argmax_correct": sum(a == r["expected"] for a, r in zip(argmax, sub)),
            "automatic": len(auto),
            "automatic_correct": sum(d == e for d, e in auto),
            "automatic_errors": sum(d != e for d, e in auto),
            "deferred": len(sub) - len(auto),
            "p50_ms": round(statistics.median(ms), 1),
            "p95_ms": round(ms[min(len(ms) - 1, int(0.95 * len(ms)))], 1),
        }
        errors = {}
        for d, r in zip(decided, sub):
            if d not in {r["expected"], "defer"}:
                errors[f"{r['expected']}->{d}"] = errors.get(f"{r['expected']}->{d}", 0) + 1
        entry["error_kinds"] = errors
        said = [(d, r) for d, r in zip(decided, sub) if r.get("said")]
        if said:
            entry["said_by_marco"] = {
                "count": len(said),
                "automatic_correct": sum(d == r["expected"] for d, r in said),
                "deferred": sum(d == "defer" for d, _ in said),
            }
        out["/".join(key)] = entry
    return out


def report(args):
    texts = {(c["task"], c["id"]): c["text"] for c in load_cases()}
    result = {}
    for path in args.scores:
        data = json.loads(Path(path).read_text())
        for r in data["rows"]:
            r["_text"] = texts.get((r["task"], r["id"]))
        result[Path(path).stem] = {"meta": data["meta"], "splits": summarize(data["rows"])}
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("train")
    t.add_argument("--out", required=True)
    t.add_argument("--real", action="store_true", help="also fit on the private real train split")
    t.add_argument("--playback", action="store_true", help="add generated echo/talk-over cases")
    t.add_argument("--epochs", type=int, default=4)
    t.add_argument("--batch", type=int, default=4)
    t.add_argument("--lr", type=float, default=5e-5)
    t.add_argument("--rank", type=int, default=16)
    t.add_argument("--scale", type=float, default=20.0)
    t.add_argument("--dropout", type=float, default=0.05)
    t.add_argument("--layers", type=int, default=28)
    t.add_argument("--seed", type=int, default=0)
    s = sub.add_parser("score")
    s.add_argument("backend", choices=["mlx", "runtime", "llama", "torch", "laya"])
    s.add_argument("--model", required=True, help="fused model dir, GGUF file or Laya bundle")
    s.add_argument("--task", action="append", choices=list(LABELS))
    s.add_argument("--head", help="laya: an ablation intent head from laya-head")
    s.add_argument("--out", required=True)
    h = sub.add_parser("laya-head", help="ablation: refit the Laya RBF intent head")
    h.add_argument("--model", required=True, help="Laya typed-decisions bundle")
    h.add_argument("--real", action="store_true")
    h.add_argument("--out", required=True)
    r = sub.add_parser("report")
    r.add_argument("scores", nargs="+")
    r.add_argument("--out")
    args = parser.parse_args()
    if args.command == "train":
        train(args)
    elif args.command == "laya-head":
        laya_head(args)
    elif args.command == "score":
        {"mlx": score_mlx, "runtime": score_runtime, "llama": score_llama, "torch": score_torch,
         "laya": score_laya}[args.backend](args)
    else:
        report(args)


if __name__ == "__main__":
    main()
