"""Named LLM presets layered on top of provider profiles.

A profile (see ``profile_manager.py``) conflates three things: the
provider connection (api_key/base_url/auth), the model choice, and
sampling params. A **loadout** is a thin layer on top -- a named preset
of provider profile + model + params (temperature, effort, max_tokens).
Loadouts never touch the profile schema; activating one just tells an
existing profile which model/params to use.

Two flavors:

    * implicit -- every model in the bundled registry
      (``model_registry.list_models_for_provider``) is automatically a
      usable loadout, named after the model, IF the user has a configured
      provider profile for that model's provider. Nothing to create,
      nothing saved to config -- synthesized on the fly from
      ``provider_profiles()`` x the registry.
    * explicit -- saved under ``kollabor.llm.loadouts.<name>`` in config,
      for when a user wants overrides (a custom temperature/effort/
      max_tokens, or a name that doesn't match any model). An explicit
      loadout shadows an implicit one of the same name.

``resolve()`` is the forgiving entry point (exact -> case-insensitive ->
unique substring, with suggestions on failure) meant for CLI/command
input; ``get()`` is the strict exact-name lookup for programmatic use.
"""

import asyncio
import logging
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any, Dict, List, Optional, Tuple

from kollabor_ai.model_registry import list_models_for_provider

logger = logging.getLogger(__name__)

# custom/local endpoints (Ollama, LM Studio, vLLM, llama.cpp, self-hosted
# gateways) can be keyless, so an endpoint alone counts as "configured"
# for those providers -- everyone else needs a real credential.
_LOCAL_ENDPOINT_PROVIDERS = {"custom", "local"}

# Transport-vs-catalog aliasing: the ChatGPT OAuth transport stores provider
# "openai_responses", but the registry tags those models "openai". Without the
# alias, an OAuth-only setup synthesizes zero implicit loadouts.
_REGISTRY_PROVIDER_ALIASES = {"openai_responses": "openai"}


@dataclass
class Loadout:
    """A named preset: provider profile + model + params.

    Implicit loadouts are synthesized on every ``list_loadouts()``/
    ``resolve()`` call from the model registry -- they are never written
    to config. ``temperature``/``max_tokens`` of ``None`` and ``effort``
    of ``""`` mean "use the provider profile's own value"; only explicit
    loadouts persist overrides for those fields.
    """

    name: str
    provider_profile: str  # name of the existing profile carrying the connection
    model: str
    temperature: Optional[float] = None  # None = provider profile's default
    effort: str = ""
    max_tokens: Optional[int] = None
    description: str = ""
    implicit: bool = False  # synthesized from model registry, not saved in config


class LoadoutManager:
    """Resolves, persists, and activates loadouts on top of a ProfileManager."""

    CONFIG_KEY = "kollabor.llm.loadouts"
    DEFAULT_CONFIG_KEY = "kollabor.llm.default_loadout"

    def get_default(self) -> Optional[str]:
        """Return the persisted default loadout name, if configured."""
        if self.config is None:
            return None
        raw = self.config.get(self.DEFAULT_CONFIG_KEY)
        if isinstance(raw, dict):
            name = raw.get("name")
        else:
            name = raw
        return str(name).strip() if name else None

    def set_default(self, name: str, level: str = "global") -> bool:
        """Persist a loadout name as the startup default at global/project level."""
        if not name or not name.strip() or self.resolve(name)[0] is None:
            logger.error("Cannot set unknown loadout as default: %s", name)
            return False
        if self.config is None:
            logger.error("No config available to persist default loadout")
            return False
        if level not in {"global", "project"}:
            raise ValueError("level must be 'global' or 'project'")
        return bool(self.config.save_key(
            self.DEFAULT_CONFIG_KEY,
            {"name": name.strip(), "level": level},
            save_target="local" if level == "project" else "global",
        ))

    def __init__(self, profile_manager: Any, config: Optional[Any] = None) -> None:
        """
        Args:
            profile_manager: ProfileManager instance loadouts are applied to.
            config: Config handle exposing ``get(key, default)`` and
                ``save_key(key, value, save_target=None)`` (e.g. a
                ``ConfigService``). Defaults to ``profile_manager.config``
                when not given.
        """
        self.profile_manager = profile_manager
        self.config: Optional[Any] = (
            config if config is not None else getattr(profile_manager, "config", None)
        )

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def provider_profiles(self) -> List[Any]:
        """Profiles that represent a usable, configured provider connection.

        A profile counts if it has a provider AND (an API key OR oauth
        auth OR -- for custom/local endpoints, which may be keyless -- a
        configured endpoint). This excludes placeholder/unconfigured
        profiles (e.g. the built-in "default" profile with no credentials).
        """
        result: List[Any] = []
        for profile in self.profile_manager.list_profiles():
            provider = (profile.get_provider() or "").lower()
            if not provider:
                continue
            has_key = bool(profile.get_api_key())
            is_oauth = getattr(profile, "auth_type", "") == "oauth"
            is_local_with_endpoint = provider in _LOCAL_ENDPOINT_PROVIDERS and bool(
                profile.get_endpoint()
            )
            if has_key or is_oauth or is_local_with_endpoint:
                result.append(profile)
        return result

    def _raw_explicit_dict(self) -> Dict[str, Any]:
        """Raw ``kollabor.llm.loadouts`` config dict, name -> stored fields."""
        if self.config is None:
            return {}
        raw = self.config.get(self.CONFIG_KEY, {}) or {}
        return dict(raw) if isinstance(raw, dict) else {}

    def _explicit_loadouts(self) -> Dict[str, Loadout]:
        """Explicit loadouts parsed from config, keyed by name."""
        result: Dict[str, Loadout] = {}
        for name, data in self._raw_explicit_dict().items():
            if not isinstance(data, dict):
                continue
            provider_profile = data.get("provider_profile", "")
            model = data.get("model", "")
            if not provider_profile or not model:
                logger.warning(
                    f"Loadout '{name}': missing provider_profile or model, skipping"
                )
                continue
            result[name] = Loadout(
                name=name,
                provider_profile=provider_profile,
                model=model,
                temperature=data.get("temperature"),
                effort=data.get("effort", ""),
                max_tokens=data.get("max_tokens"),
                description=data.get("description", ""),
                implicit=False,
            )
        return result

    def list_loadouts(self) -> List[Loadout]:
        """Explicit loadouts (sorted by name), then implicit ones.

        Implicit loadouts are synthesized from ``provider_profiles()`` x the
        model registry, then x the provider's cached live catalog, in that
        order (provider-profile order, then the registry's curated per-provider
        order, then catalog order). An implicit loadout is dropped when its
        name collides with an explicit loadout or with an implicit already
        emitted for an earlier provider profile -- first provider profile wins.

        The catalog half is read from cache only, so this stays synchronous and
        never blocks. A provider whose catalog has not been fetched yet simply
        contributes its registry models (possibly none) -- call
        ``refresh_catalogs()`` to warm it. This is what lets proxy providers
        with no bundled registry entries (OpenRouter) appear at all.
        """
        explicit = self._explicit_loadouts()
        result: List[Loadout] = sorted(explicit.values(), key=lambda lo: lo.name)
        seen = set(explicit.keys())

        for profile in self.provider_profiles():
            provider = (profile.get_provider() or "").lower()
            registry_provider = _REGISTRY_PROVIDER_ALIASES.get(provider, provider)
            model_names = [
                name for name, _info in list_models_for_provider(registry_provider)
            ]
            model_names.extend(
                str(entry.get("id") or "")
                for entry in self._cached_catalog(profile)
                if entry.get("id")
            )
            for model_name in model_names:
                if model_name in seen:
                    continue
                seen.add(model_name)
                result.append(
                    Loadout(
                        name=model_name,
                        provider_profile=profile.name,
                        model=model_name,
                        implicit=True,
                    )
                )
        return result

    @staticmethod
    def _cached_catalog(profile: Any) -> List[Dict[str, Any]]:
        """This profile's cached live catalog. Empty when cold or unavailable."""
        try:
            from kollabor_ai.model_catalog import cached_provider_models

            return list(cached_provider_models(profile))
        except Exception as exc:  # noqa: BLE001 -- catalog must never break listing
            logger.debug("cached catalog unavailable for %s: %s", profile, exc)
            return []

    async def refresh_catalogs(self) -> Dict[str, int]:
        """Fetch every configured provider's live catalog into the cache.

        Runs the fetches concurrently -- wall-clock is the slowest provider,
        not their sum. Returns ``{profile_name: model_count}``; a provider that
        fails or has no listing API reports 0 and is not an error. Callers
        re-read ``list_loadouts()`` afterwards to pick the results up.
        """
        try:
            from kollabor_ai.model_catalog import get_provider_models
        except Exception as exc:  # noqa: BLE001
            logger.warning("model catalog unavailable: %s", exc)
            return {}

        profiles = self.provider_profiles()
        if not profiles:
            return {}

        results = await asyncio.gather(
            *(get_provider_models(profile) for profile in profiles),
            return_exceptions=True,
        )

        counts: Dict[str, int] = {}
        for profile, models in zip(profiles, results):
            if isinstance(models, BaseException):
                logger.warning("catalog fetch failed for %s: %s", profile.name, models)
                counts[profile.name] = 0
            else:
                counts[profile.name] = len(models)
        logger.info("loadout catalogs refreshed: %s", counts)
        return counts

    def get(self, name: str) -> Optional[Loadout]:
        """Exact-name lookup (explicit or implicit)."""
        for loadout in self.list_loadouts():
            if loadout.name == name:
                return loadout
        return None

    def resolve(self, query: str) -> Tuple[Optional[Loadout], List[str]]:
        """Forgiving name resolution for CLI/command input.

        Resolution order: exact explicit -> exact implicit -> case-
        insensitive exact -> unique case-insensitive substring match.

        Returns:
            ``(loadout, [])`` on a match. ``(None, suggestions)`` on no
            unique match, where suggestions is up to 8 loadout names
            containing the query case-insensitively, or the closest names
            by edit distance when none contain it.
        """
        if not query:
            return None, []

        loadouts = self.list_loadouts()

        for loadout in loadouts:
            if not loadout.implicit and loadout.name == query:
                return loadout, []
        for loadout in loadouts:
            if loadout.implicit and loadout.name == query:
                return loadout, []

        query_lower = query.lower()
        for loadout in loadouts:
            if loadout.name.lower() == query_lower:
                return loadout, []

        contains = [lo for lo in loadouts if query_lower in lo.name.lower()]
        if len(contains) == 1:
            return contains[0], []

        if contains:
            return None, [lo.name for lo in contains][:8]

        names = [lo.name for lo in loadouts]
        return None, get_close_matches(query, names, n=8, cutoff=0.4)

    # ------------------------------------------------------------------
    # Writing (explicit loadouts only)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_loadout_dict(
        provider_profile: str,
        model: str,
        temperature: Optional[float],
        effort: str,
        max_tokens: Optional[int],
        description: str,
    ) -> Dict[str, Any]:
        """Non-default fields only -- provider_profile + model are always kept."""
        data: Dict[str, Any] = {"provider_profile": provider_profile, "model": model}
        if temperature is not None:
            data["temperature"] = temperature
        if effort:
            data["effort"] = effort
        if max_tokens is not None:
            data["max_tokens"] = max_tokens
        if description:
            data["description"] = description
        return data

    def create(
        self,
        name: str,
        provider_profile: str,
        model: str,
        temperature: Optional[float] = None,
        effort: str = "",
        max_tokens: Optional[int] = None,
        description: str = "",
    ) -> bool:
        """Create a new explicit loadout.

        Fails on an empty name, a name that already has an explicit
        loadout, or a ``provider_profile`` that doesn't exist.
        """
        if not name or not name.strip():
            logger.error("Loadout name cannot be empty")
            return False

        raw = self._raw_explicit_dict()
        if name in raw:
            logger.error(f"Loadout already exists: {name}")
            return False

        if self.profile_manager.get_profile(provider_profile) is None:
            logger.error(f"Unknown provider profile: {provider_profile}")
            return False

        if self.config is None:
            logger.error("No config available to persist loadout")
            return False

        # Mutate the whole "loadouts" dict and write it back as one key
        # (rather than a dotted "loadouts.<name>" path) so loadout names
        # that contain dots -- e.g. shadowing the implicit "gpt-5.6" --
        # are stored as a literal key instead of being split into nested
        # dicts by dot-path traversal.
        raw[name] = self._build_loadout_dict(
            provider_profile, model, temperature, effort, max_tokens, description
        )
        return bool(self.config.save_key(self.CONFIG_KEY, raw))

    def update(self, name: str, **fields: Any) -> bool:
        """Update an existing explicit loadout. Refuses implicit/unknown names."""
        existing = self._explicit_loadouts().get(name)
        if existing is None:
            logger.error(f"Loadout not found (or not explicit): {name}")
            return False

        allowed = {
            "provider_profile",
            "model",
            "temperature",
            "effort",
            "max_tokens",
            "description",
        }
        unknown = set(fields) - allowed
        if unknown:
            logger.error(f"Unknown loadout field(s): {', '.join(sorted(unknown))}")
            return False

        provider_profile = fields.get("provider_profile", existing.provider_profile)
        if "provider_profile" in fields and (
            self.profile_manager.get_profile(provider_profile) is None
        ):
            logger.error(f"Unknown provider profile: {provider_profile}")
            return False

        if self.config is None:
            logger.error("No config available to persist loadout")
            return False

        raw = self._raw_explicit_dict()
        raw[name] = self._build_loadout_dict(
            provider_profile,
            fields.get("model", existing.model),
            fields.get("temperature", existing.temperature),
            fields.get("effort", existing.effort),
            fields.get("max_tokens", existing.max_tokens),
            fields.get("description", existing.description),
        )
        return bool(self.config.save_key(self.CONFIG_KEY, raw))

    def delete(self, name: str) -> bool:
        """Delete an explicit loadout. Refuses implicit/unknown names."""
        raw = self._raw_explicit_dict()
        if name not in raw:
            logger.error(f"Loadout not found (or not explicit): {name}")
            return False

        if self.config is None:
            logger.error("No config available to persist loadout")
            return False

        del raw[name]
        return bool(self.config.save_key(self.CONFIG_KEY, raw))

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    async def activate(
        self, loadout_or_name: Any, event_bus: Optional[Any] = None
    ) -> Optional[Loadout]:
        """Apply a loadout to its provider profile and activate it.

        Applies the loadout's set fields (model always; temperature/
        effort/max_tokens only when the loadout overrides them) to its
        provider profile via
        ``profile_manager.update_profile(..., save_to_config=True)``, then
        activates that provider profile -- through ``state_service`` when
        one is wired on ``event_bus`` (attach/daemon mode), otherwise
        directly plus the ``llm_service`` reinitialize/reload-tools
        fallback (mirrors
        ``handlers/model.py:ModelCommandHandler._set_active_profile_model``).

        Args:
            loadout_or_name: A loadout name to resolve, or an already-
                resolved ``Loadout`` instance.
            event_bus: Optional event bus used to look up "state_service"
                and "llm_service" for activation.

        Returns:
            The resolved Loadout, or None if resolution or the profile
            update failed.
        """
        if isinstance(loadout_or_name, str):
            loadout, _suggestions = self.resolve(loadout_or_name)
        else:
            loadout = loadout_or_name

        if loadout is None:
            return None

        update_kwargs: Dict[str, Any] = {
            "model": loadout.model,
            "save_to_config": True,
        }
        if loadout.temperature is not None:
            update_kwargs["temperature"] = loadout.temperature
        if loadout.effort:
            update_kwargs["effort"] = loadout.effort
        if loadout.max_tokens is not None:
            update_kwargs["max_tokens"] = loadout.max_tokens

        if not self.profile_manager.update_profile(
            loadout.provider_profile, **update_kwargs
        ):
            logger.error(
                f"Loadout '{loadout.name}': failed to update provider "
                f"profile '{loadout.provider_profile}'"
            )
            return None

        state_service = None
        if event_bus and hasattr(event_bus, "get_service"):
            state_service = event_bus.get_service("state_service")

        synced = False
        if state_service and hasattr(state_service, "set_active_profile"):
            try:
                await state_service.set_active_profile(
                    loadout.provider_profile, reload_profile=True
                )
                synced = True
            except Exception as exc:  # noqa: BLE001 -- fall back to direct activation
                logger.warning(f"state-service loadout activation failed: {exc}")

        if not synced:
            self.profile_manager.set_active_profile(loadout.provider_profile)
            llm_service = None
            if event_bus and hasattr(event_bus, "get_service"):
                llm_service = event_bus.get_service("llm_service")
            if llm_service and hasattr(llm_service, "api_service"):
                profile = self.profile_manager.get_active_profile()
                await llm_service.api_service.reinitialize_provider(profile)
                await llm_service._load_native_tools()

        return loadout
