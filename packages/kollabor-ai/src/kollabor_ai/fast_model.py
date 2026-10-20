"""Fast, cheap model override for background work (voice replies, dreaming).

Unset means "use the session model": every helper returns the provider it was
given untouched, so the default behaviour never changes.
"""

import copy
from typing import Any, Optional

SHARED_KEY = "kollabor.llm.fast_model"


def resolve_fast_model(config: Any, specific_key: str) -> Optional[str]:
    """Model id for one task: its own key, else the shared fast model, else None."""
    if config is None:
        return None
    for key in (specific_key, SHARED_KEY):
        value = config.get(key, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def with_model(provider: Any, model: Optional[str]) -> Any:
    """A copy of `provider` that calls `model`; the original is never mutated.

    Same credentials and endpoint (the model must exist on the active profile's
    provider). The copy shares the HTTP client, so it is safe beside the
    session's own calls.
    """
    if not model or model == getattr(provider, "model", None):
        return provider
    clone = copy.copy(provider)
    clone.model = model
    clone.config = provider.config.model_copy(update={"model": model})
    return clone
