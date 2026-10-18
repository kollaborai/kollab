"""System Prompts page routes: list, inspect, override, preview.

Overrides live in the user space (see kollabor_ai.prompt_overrides) and
are honored by the real prompt path — PromptRenderer consults the store
when it resolves section includes, so a PUT here changes what agents
receive on their next prompt build. Bundled files are never modified.
"""

from typing import Any, Dict, Optional

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

_walker: Optional[PromptSectionWalker] = None
_store: PromptOverrideStore = PromptOverrideStore()


def get_walker() -> PromptSectionWalker:
    """Walker used by the routes (tests may swap it via set_walker).

    Unless one was set, each request gets a new one over a fresh AgentManager, the
    resolver POST /sessions and GET /agents/bundles use: a bundle resolves local,
    then global, then bundled, and an agent made since the last request shows up.
    """
    if _walker is not None:
        return _walker
    from kollabor_agent.agent_manager import AgentManager

    return PromptSectionWalker(store=get_store(), agent_manager=AgentManager())


def get_store() -> PromptOverrideStore:
    """Store used by the routes (tests may swap it via set_store)."""
    return _store


def set_walker(walker: Optional[PromptSectionWalker]) -> None:
    global _walker
    _walker = walker


def set_store(store: PromptOverrideStore) -> None:
    global _store
    _store = store


def _resolve_bundle(bundle: str, walker: PromptSectionWalker) -> Any:
    """AgentRuntime for an agent bundle, or None for the default bundle."""
    if bundle == DEFAULT_BUNDLE:
        return None
    try:
        agent = walker.agent_manager.get_agent(bundle)
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
        for agent in get_walker().agent_manager.list_agents():
            name = str(getattr(agent, "name", "") or "")
            if not name or name == DEFAULT_BUNDLE:  # the default row below is it
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
    walker = get_walker()
    _resolve_bundle(bundle, walker)
    try:
        sections = walker.walk(bundle)
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
    walker = get_walker()
    _resolve_bundle(bundle, walker)
    try:
        data = walker.section_content(bundle, key)
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
    walker = get_walker()
    _resolve_bundle(bundle, walker)
    content = body.get("content")
    if not isinstance(content, str):
        raise HTTPException(
            status_code=422, detail='body must be {"content": "..."} (string)'
        )
    try:
        sections = {s.key: s for s in walker.walk(bundle)}
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
    _resolve_bundle(bundle, get_walker())
    deleted = get_store().delete_override(bundle, key)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"no override exists for {bundle}/{key}"
        )
    return {"bundle": bundle, "key": key, "overridden": False}


@router.get("/{bundle}/preview")
async def preview_prompt(bundle: str) -> Dict[str, Any]:
    """Rendered prompt with overrides applied (what the agent would get)."""
    walker = get_walker()
    agent = _resolve_bundle(bundle, walker)
    try:
        from kollabor_ai.prompt_renderer import render_system_prompt

        if agent is not None:
            text, base_dir = walker.base_prompt_info(bundle)
        else:
            from kollabor_config.config_utils import (
                get_system_prompt_content,
                get_system_prompt_path,
            )

            text = get_system_prompt_content()
            base_dir = get_system_prompt_path().parent
        rendered = render_system_prompt(
            text,
            base_path=base_dir,
            agent_manager=walker.agent_manager,
            prompt_bundle=bundle,
            override_store=get_store(),
        )
    except SectionInfoError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"prompt preview failed: {exc}")
    return {"bundle": bundle, "tokens": count_tokens(rendered), "prompt": rendered}
