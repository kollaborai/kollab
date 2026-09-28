"""Voice intent head over frozen Laya encoder representations.

Only the small voice decision head is trained. The pinned Laya checkpoint and
its tokenizer remain unchanged. This module is imported only in the worker.
"""

from pathlib import Path

from .classifiers import LAYA_QUESTION


def features(agent, state, questions=LAYA_QUESTION):
    import torch
    from laya.common import build_sequence, collate_items

    question = agent._to_internal(next(iter(questions.values())))
    ids, markers = build_sequence(
        agent.tok, state, question, agent.cfg["max_len"], agent.cfg["head_max_len"]
    )
    item = {"ids": ids, "markers": markers, "qtype": 0}
    batch = collate_items([[item]], agent.tok.pad_token_id)
    with torch.inference_mode():
        hidden = agent.model.encoder(
            input_ids=batch["input_ids"].to(agent.device),
            attention_mask=batch["attention_mask"].to(agent.device),
        ).last_hidden_state[0]
        separators = [
            i for i, token in enumerate(ids) if token == agent.tok.sep_token_id
        ]
        state_start = separators[1] + 1
        state_mean = hidden[state_start:-1].mean(dim=0)
        return torch.cat(
            (hidden[0], hidden[markers[0]] - hidden[markers[1]], state_mean)
        ).float()


class VoiceIntentModel:
    def __init__(self, agent, head_path: Path, questions=LAYA_QUESTION, spec=None):
        import torch
        from safetensors.torch import load_file

        self.agent = agent
        self.questions = questions
        self.answer_key = next(iter(questions))
        self.labels = list(questions[self.answer_key]["criteria"])
        self.cfg, self.tok = agent.cfg, agent.tok
        weights = load_file(str(head_path))
        self.mean = weights["mean"].to(agent.device)
        self.centres = weights["centres"].to(agent.device)
        self.alpha = weights["alpha"].to(agent.device)
        from .control import manifest

        spec = spec or manifest()["classifiers"]["laya"]["voice_head"]
        self.gamma = spec["kernel_gamma"]
        self.model_name = head_path.stem
        self.torch = torch

    def predict(self, state, questions):
        if questions != self.questions:
            raise ValueError(
                "Voice intent head requires its versioned question contract"
            )
        vector = features(self.agent, state, self.questions) - self.mean
        vector = vector / vector.norm().clamp_min(1e-12)
        kernels = self.torch.exp(
            -self.gamma * ((self.centres - vector) ** 2).sum(dim=-1)
        )
        probability = float(self.torch.sigmoid(kernels @ self.alpha).cpu())
        return {
            "model": self.model_name,
            "answers": {
                self.answer_key: {
                    "choice": self.labels[0] if probability >= 0.5 else self.labels[1],
                    "probabilities": {
                        self.labels[0]: probability,
                        self.labels[1]: 1 - probability,
                    },
                },
            },
        }
