"""Deep Thought Engine - transparent multi-instance parallel reasoning.

Automatically spawns parallel kollabor instances to ponder questions
from multiple methodological angles, synthesizes their reasoning,
and injects enriched context back into the main agent's conversation.

The model never knows this is happening. It just thinks better.
"""

from .plugin import DeepThoughtPlugin

__all__ = ["DeepThoughtPlugin"]
