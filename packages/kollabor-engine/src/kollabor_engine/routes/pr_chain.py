"""Read-only PR-chain routes: an external runner's progress for the web UI sidebar (#1008)."""

from typing import Any, Dict, Literal

from fastapi import APIRouter, HTTPException  # type: ignore[import-not-found]

from .. import pr_chain

router = APIRouter(prefix="/pr-chain", tags=["pr-chain"])


@router.get("")
def list_pr_chain() -> Dict[str, Any]:
    base = pr_chain.root()
    items, truncated = pr_chain.list_items(base)
    return {
        "root_found": base.is_dir(),
        "items": items,
        "events": pr_chain.read_events(base),
        "truncated": truncated,
    }


@router.get("/{kind}/{number}")
def pr_chain_item(kind: Literal["pr", "issue"], number: int) -> Dict[str, Any]:
    base = pr_chain.root()
    item = pr_chain.read_item(base, kind, number)
    if item is None:
        raise HTTPException(status_code=404, detail=f"No {kind} {number} in the runner folder")
    return {"item": item, "events": pr_chain.read_events(base, kind, number)}
