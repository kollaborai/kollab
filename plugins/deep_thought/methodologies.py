"""Thinking methodologies for Deep Thought Engine.

Each methodology defines a system prompt modifier that steers a child
instance to approach the question from a specific angle. The orchestrator
picks N methodologies per question and fans out parallel instances.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Methodology:
    """A thinking methodology that steers a child instance."""

    name: str
    description: str
    system_prompt: str
    weight: float = 1.0  # relative selection probability
    tags: List[str] = field(default_factory=list)


# Core methodologies - the engine picks from these based on question type
METHODOLOGIES = [
    Methodology(
        name="first_principles",
        description="Break down to fundamental truths, reason up",
        system_prompt=(
            "You are a first-principles thinker. Break this question down to its "
            "most fundamental truths and assumptions. Challenge every assumption. "
            "Reason upward from base facts to reach your conclusion. Be rigorous "
            "and precise. Show your reasoning chain clearly."
        ),
        weight=1.5,
        tags=["analytical", "deep"],
    ),
    Methodology(
        name="devils_advocate",
        description="Find weaknesses, counterarguments, failure modes",
        system_prompt=(
            "You are a devil's advocate. Your job is to find every weakness, "
            "counterargument, and failure mode in the most obvious answer to "
            "this question. Think about edge cases, hidden assumptions, and "
            "scenarios where the naive approach breaks down. Be thorough but "
            "constructive - identify problems AND suggest mitigations."
        ),
        weight=1.2,
        tags=["critical", "risk"],
    ),
    Methodology(
        name="pragmatic",
        description="Focus on practical implementation and tradeoffs",
        system_prompt=(
            "You are a pragmatic engineer. Focus on what actually works in "
            "practice. Consider implementation complexity, maintenance burden, "
            "real-world constraints, and the 80/20 rule. Don't over-engineer. "
            "What's the simplest thing that could possibly work well? What are "
            "the real tradeoffs?"
        ),
        weight=1.3,
        tags=["practical", "engineering"],
    ),
    Methodology(
        name="creative",
        description="Lateral thinking, unconventional approaches",
        system_prompt=(
            "You are a lateral thinker. Look at this from angles nobody else "
            "would consider. What if we inverted the problem? What analogies "
            "from completely different domains apply here? What would a "
            "10x solution look like vs an incremental one? Don't be constrained "
            "by conventional approaches."
        ),
        weight=0.8,
        tags=["creative", "innovative"],
    ),
    Methodology(
        name="user_empathy",
        description="Think from the end user's perspective",
        system_prompt=(
            "You are a user advocate. Think about this entirely from the "
            "perspective of the person who will use the result. What do they "
            "actually need? What would frustrate them? What would delight them? "
            "What are they really asking for underneath the literal question? "
            "Focus on the human experience."
        ),
        weight=1.0,
        tags=["ux", "human"],
    ),
    Methodology(
        name="systems_thinking",
        description="Consider the whole system, feedback loops, emergent behavior",
        system_prompt=(
            "You are a systems thinker. Consider this question in the context "
            "of the larger system it lives in. What are the feedback loops? "
            "What are the second and third-order effects? How does this interact "
            "with other components? What emergent behaviors might arise? "
            "Think about dependencies, coupling, and cascading effects."
        ),
        weight=1.0,
        tags=["architecture", "holistic"],
    ),
    Methodology(
        name="historical",
        description="Learn from prior art and past mistakes",
        system_prompt=(
            "You are a historian of engineering decisions. What prior art exists "
            "for this kind of problem? What approaches have been tried before "
            "and what were the outcomes? What common mistakes do people make "
            "with this type of problem? What can we learn from how similar "
            "challenges were solved in other contexts?"
        ),
        weight=0.7,
        tags=["experience", "patterns"],
    ),
    Methodology(
        name="adversarial",
        description="Think like an attacker, find security and reliability issues",
        system_prompt=(
            "You are a security-minded reviewer. Think about how this could "
            "fail, be exploited, or produce unexpected behavior. Consider race "
            "conditions, edge cases, malicious inputs, resource exhaustion, and "
            "failure modes. What happens when things go wrong? How do we make "
            "this robust and resilient?"
        ),
        weight=0.9,
        tags=["security", "reliability"],
    ),
]


def select_methodologies(
    count: int = 3, tags: Optional[List[str]] = None
) -> List[Methodology]:
    """Select N methodologies, optionally filtered by tags.

    Uses weighted random selection to pick diverse approaches.

    Args:
        count: Number of methodologies to select.
        tags: Optional tag filter (select only methodologies matching these tags).

    Returns:
        List of selected Methodology objects.
    """
    import random

    pool = METHODOLOGIES
    if tags:
        pool = [m for m in pool if any(t in m.tags for t in tags)]

    if not pool:
        pool = METHODOLOGIES

    # Weighted selection without replacement
    selected = []
    remaining = list(pool)
    for _ in range(min(count, len(remaining))):
        weights = [m.weight for m in remaining]
        total = sum(weights)
        if total == 0:
            break
        probs = [w / total for w in weights]
        idx = random.choices(range(len(remaining)), weights=probs, k=1)[0]
        selected.append(remaining.pop(idx))

    return selected
