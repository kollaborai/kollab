"""Per-request tuning params: sampling and reasoning effort.

One place decides *whether* to send temperature/top_p and *how* to spell
reasoning effort, because every provider that builds a payload needs the same
two decisions and getting either wrong is a 400:

  * newer reasoning models reject temperature/top_p/top_k -- the registry marks
    them ``supports_sampling: false`` and we omit the params entirely
  * effort is opt-in and provider-specific in shape; unset means send nothing

Usage in a payload builder::

    params.update(sampling_params(self.config, self.model))
    params.update(effort_params(self.config, EffortStyle.OPENAI))
"""

import logging
from typing import Any, Dict

from ..model_registry import supports_sampling

logger = logging.getLogger(__name__)


class EffortStyle:
    """How a provider spells reasoning effort on the wire."""

    OPENAI = "openai"  # chat completions: reasoning_effort: "high"
    ANTHROPIC = "anthropic"  # output_config: {"effort": "high"}
    RESPONSES = "responses"  # reasoning: {"effort": "high"}


# Providers whose payload builders actually send effort. Anything else silently
# drops it, so /model effort must not claim success for those.
EFFORT_SUPPORTED_PROVIDERS = frozenset(
    {"anthropic", "openai", "azure_openai", "openai_responses", "openrouter", "custom"}
)


def sampling_params(config: Any, model: str) -> Dict[str, Any]:
    """``{"temperature": ..., "top_p": ...}``, or ``{}`` for reasoning models.

    Returns an empty dict when the model rejects sampling params, so callers can
    ``update()`` unconditionally. top_p is only included when explicitly set.
    """
    if not supports_sampling(model):
        return {}

    params: Dict[str, Any] = {"temperature": getattr(config, "temperature", None)}
    if params["temperature"] is None:
        params.pop("temperature")
    top_p = getattr(config, "top_p", None)
    if top_p is not None:
        params["top_p"] = top_p
    return params


def effort_params(config: Any, style: str) -> Dict[str, Any]:
    """The provider-shaped effort field, or ``{}`` when no effort is set."""
    effort = getattr(config, "effort", None)
    if not effort:
        return {}
    if style == EffortStyle.ANTHROPIC:
        return {"output_config": {"effort": effort}}
    if style == EffortStyle.RESPONSES:
        return {"reasoning": {"effort": effort}}
    return {"reasoning_effort": effort}
