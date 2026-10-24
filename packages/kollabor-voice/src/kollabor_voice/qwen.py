"""Device-owned worker for the fine-tuned Qwen3-0.6B voice model (qwen_worker.py)."""

from __future__ import annotations

from .classifiers import CLASSIFIERS
from .laya import LayaWorker
from .speech_settings import apple_silicon


class QwenWorker(LayaWorker):
    """Laya's lifecycle in its own pinned MLX runtime; also answers "was that for me?"."""

    name = "qwen"
    label = CLASSIFIERS["qwen"]
    module = "kollabor_voice.qwen_worker"
    imports = "import mlx.core, mlx_lm"
    warming = f"Loading and warming {CLASSIFIERS['qwen']}"

    def prepare(self):
        if not apple_silicon():
            self.state = {
                "state": "error",
                "detail": f"{self.label} needs an Apple silicon Mac",
            }
            return
        super().prepare()

    async def answer(self, held, question, text):
        return await self._exchange(
            {
                "task": "answer",
                "held": held[-2000:],
                "question": question[:200],
                "text": text[-2000:],
            }
        )
