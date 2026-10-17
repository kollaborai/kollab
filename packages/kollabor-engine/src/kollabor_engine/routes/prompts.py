"""System Prompts page routes: list, inspect, override, preview.

Overrides live in the user space (see kollabor_ai.prompt_overrides) and
are honored by the real prompt path — PromptRenderer consults the store
when it resolves section includes, so a PUT here changes what agents
receive on their next prompt build. Bundled files are never modified.
"""

from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException  # type: ignore[import-not-found]

from kollabor_ai.prompt_overrides import (
    DEFAULT_BUNDLE,
    PromptOverrideError,
    PromptOverrideStore,
)
from kollabor_ai.prompt_sections import (
    PromptSectionWalker,
    SectionInfoError,
    count_tokens,
)

router = APIRouter(prefix="/prompts", tags=["prompts"])

_walker: PromptSectionWalker = PromptSectionWalker()
_store: PromptOverrideStore = PromptOverrideStore()


def get_walker() -> PromptSectionWalker:
    """Walker used by the routes (tests may swap it via set_walker)."""
    return _walker


def get_store() -> PromptOverrideStore:
    """Store used by the routes (tests may swap it via set_store)."""
    return _store


def set_walker(walker: PromptSectionWalker) -> None:
    global _walker
    _walker = walker


def set_store(store: PromptOverrideStore) -> None:
    global _store
    _store = store


def _resolve_bundle(bundle: str) -> Any:
    """AgentRuntime for an agent bundle, or None for the default bundle.

    Prefers the walker's agent manager (same source as section assembly),
    falling back to a real AgentManager for production callers.
    """
    if bundle == DEFAULT_BUNDLE:
        return None
    agent = None
    walker_manager = getattr(get_walker(), "agent_manager", None)
    if walker_manager is not None:
        try:
            agent = walker_manager.get_agent(bundle)
        except Exception:
            agent = None
    if agent is None:
        try:
            from kollabor_agent.agent_manager import AgentManager

            agent = AgentManager().get_agent(bundle)
        except Exception:
            agent = None
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown prompt bundle: {bundle}")
    return agent


@router.get("")
async def list_prompts() -> Dict[str, Any]:
    """All prompt bundles: every agent bundle plus the default file prompt."""
    bundles: list = []
    try:
        manager = getattr(get_walker(), "agent_manager", None)
        if manager is None:
            from kollabor_agent.agent_manager import AgentManager

            manager = AgentManager()
        for agent in manager.list_agents():
            name = str(getattr(agent, "name", "") or "")
            if not name:
                continue
            bundles.append(
                {
                    "bundle": name,
                    "description": str(getattr(agent, "description", "") or ""),
                    "source": str(getattr(agent, "source", "") or "bundled"),
                    "default": False,
                }
            )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"could not list agent bundles: {exc}"
        )
    bundles.append(
        {
            "bundle": DEFAULT_BUNDLE,
            "description": "Default file-based system prompt (agents/default)",
            "source": "bundled",
            "default": True,
        }
    )
    bundles.sort(key=lambda item: (not item["default"], item["bundle"]))
    return {"bundles": bundles, "count": len(bundles)}


@router.get("/{bundle}/sections")
async def list_sections(bundle: str) -> Dict[str, Any]:
    """Sections of a bundle's prompt in true assembly order."""
    _resolve_bundle(bundle)
    try:
        sections = get_walker().walk(bundle)
    except SectionInfoError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "bundle": bundle,
        "sections": [section.as_dict() for section in sections],
        "count": len(sections),
    }


@router.get("/{bundle}/sections/{key}")
async def get_section(bundle: str, key: str) -> Dict[str, Any]:
    """Raw content of one section (override if present, else bundled)."""
    _resolve_bundle(bundle)
    try:
        data = get_walker().section_content(bundle, key)
    except SectionInfoError as exc:
        status = 404
        raise HTTPException(status_code=status, detail=str(exc))
    overridden = get_store().has_override(bundle, key)
    return {
        "bundle": bundle,
        "key": key,
        "overridden": overridden,
        "source": "override" if overridden else data["source"],
        "content": data["content"],
        "tokens": count_tokens(data["content"]),
    }


@router.put("/{bundle}/sections/{key}")
async def put_override(
    bundle: str,
    key: str,
    body: Dict[str, Any] = Body(...),
) -> Dict[str, Any]:
    """Save a user override for an editable section. 4xx for others."""
    _resolve_bundle(bundle)
    content = body.get("content")
    if not isinstance(content, str):
        raise HTTPException(
            status_code=422, detail='body must be {"content": "..."} (string)'
        )
    try:
        sections = {s.key: s for s in get_walker().walk(bundle)}
    except SectionInfoError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    section = sections.get(key)
    if section is None:
        raise HTTPException(status_code=404, detail=f"unknown section key: {key}")
    if section.kind != "editable":
        raise HTTPException(
            status_code=400,
            detail=f"section {key} is {section.kind}, not editable; "
            "only editable section files can be overridden",
        )
    try:
        path = get_store().save_override(bundle, key, content)
    except PromptOverrideError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "bundle": bundle,
        "key": key,
        "overridden": True,
        "path": str(path),
        "tokens": count_tokens(content),
    }


@router.delete("/{bundle}/sections/{key}")
async def delete_override(bundle: str, key: str) -> Dict[str, Any]:
    """Remove a user override; the bundled content applies again."""
    _resolve_bundle(bundle)
    deleted = get_store().delete_override(bundle, key)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"no override exists for {bundle}/{key}"
        )
    return {"bundle": bundle, "key": key, "overridden": False}


@router.get("/{bundle}/preview")
async def preview_prompt(bundle: str) -> Dict[str, Any]:
    """Rendered prompt with overrides applied (what the agent would get)."""
    agent = _resolve_bundle(bundle)
    try:
        if agent is not None:
            from kollabor_ai import PromptRenderer

            renderer = PromptRenderer(
                base_path=agent.directory,
                agent_manager=None,
                skip_volatile=False,
                prompt_bundle=bundle,
                override_store=get_store(),
            )
            rendered = renderer.render(agent.system_prompt)
        else:
            from kollabor_ai.prompt_renderer import render_system_prompt
            from kollabor_config.config_utils import get_system_prompt_content

            rendered = render_system_prompt(
                get_system_prompt_content(),
                prompt_bundle=DEFAULT_BUNDLE,
                override_store=get_store(),
            )
    except SectionInfoError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"prompt preview failed: {exc}")
    return {"bundle": bundle, "tokens": count_tokens(rendered), "prompt": rendered}
