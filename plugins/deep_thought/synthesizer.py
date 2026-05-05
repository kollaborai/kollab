"""Thought Synthesizer - combines multiple perspectives into enriched context.

Takes the output from N parallel thinkers and produces a unified synthesis
that gets injected as a system message into the main conversation.
The main model never knows this happened - it just has richer context.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def synthesize_thoughts(
    question: str,
    results: Dict[str, Dict[str, Any]],
) -> str:
    """Synthesize multiple thinking perspectives into enriched context.

    Produces a system message injected into conversation history before
    the main model generates its response.

    Args:
        question: The original question/message.
        results: Dict mapping instance_id to result dict.

    Returns:
        Synthesized context string for injection as system message.
    """
    if not results:
        return ""

    parts = []
    parts.append("[Deep Thought Analysis]")
    parts.append(
        "I ran parallel analysis on this question from multiple perspectives. "
        "Here are the results - use these to give me a thorough response:"
    )
    parts.append("")

    valid_count = 0
    for instance_id, result in results.items():
        methodology = result.get("methodology", "unknown")
        content = result.get("content", "").strip()

        if not content:
            continue

        # Only truncate extremely long outputs
        max_chars = 8000
        if len(content) > max_chars:
            content = content[:max_chars] + "\n[...truncated for length]"

        label = methodology.replace("_", " ").title()
        parts.append(f"--- {label} Perspective ---")
        parts.append(content)
        parts.append("")
        valid_count += 1

    if valid_count == 0:
        return ""

    parts.append("[Instructions]")
    parts.append(
        "Synthesize the above perspectives into a single thorough response. "
        "Don't just list them separately - weave the insights together. "
        "Prioritize practical advice while noting risks. "
        "If perspectives conflict, explain the tension."
    )

    synthesis = "\n".join(parts)
    logger.info(f"Synthesized {valid_count} perspectives into {len(synthesis)} chars")
    return synthesis
