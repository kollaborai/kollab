"""Apps: the Apps picker (``apps``), MCP Registry search (``apps-registry``) and the
install form for one MCP server (``apps-install``).

The rules live in ``kollabor.apps``. These panels shape the screen and load a
saved MCP change into the running session. A secret goes in through
``apps-install`` and never comes back out: no describe shows a value and no
reply repeats one. A registry server always opens ``apps-install`` first, so
its exact command shows before anything is saved or run.
"""

from __future__ import annotations

import logging
from typing import Any

from kollabor.apps.catalog import TOKEN_RE
from kollabor.apps.registry import NOT_REVIEWED, fingerprint
from kollabor.apps.service import AppsService, is_installed, required_unset

from .base import PanelError, error_result, make_field, ok_result, unknown_action

logger = logging.getLogger(__name__)

ROW_ACTIONS = [
    {"id": "add", "label": "Add"},
    {"id": "enable", "label": "Turn On"},
    {"id": "remove", "label": "Remove", "confirm": True},
]
TOOLBAR_ACTIONS = [{"id": "refresh", "label": "Refresh"}]


def _service() -> AppsService:
    return AppsService()


async def mcp_runtime(ctx: Any) -> dict[str, dict[str, Any]]:
    """The session's MCP facts by server name. Empty when the daemon cannot say."""
    try:
        snapshot = await ctx.get_mcp_state()
    except Exception:  # noqa: BLE001 - no MCP integration in this daemon
        logger.debug("apps: MCP state unavailable")
        return {}
    return {
        server.name: {"connected": server.connected, "tool_count": server.tool_count, "error": ""}
        for server in snapshot.servers
    }


async def apply_change(ctx: Any, result: dict[str, Any]) -> None:
    """Load a saved change into the running session. A failed MCP reload is said in the message.

    The change stays saved either way, so the next start loads it.
    """
    if ctx is None:
        return
    manager = getattr(ctx, "_agent_manager", None)
    if result.get("skills_changed") and manager is not None:
        try:
            manager.refresh()  # rescans the skill library, re-resolves each agent's skills
            llm = getattr(ctx, "_llm_service", None)
            if llm is not None:
                llm.rebuild_system_prompt()  # the prompt's skill catalog lists the change
        except Exception:  # noqa: BLE001
            logger.warning("apps: skill refresh failed", exc_info=True)
            result["message"] = f"{result['message']} This session lists it after a restart."
    if not result.get("mcp_changed"):
        return
    try:
        await ctx.reload_mcp_servers()
    except Exception:  # noqa: BLE001
        logger.warning("apps: MCP reload failed", exc_info=True)
        result["message"] = f"{result['message']} Saved, but this session did not reload it. Restart to load it."


async def _target(service: AppsService, row_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The row and the entry behind it. A row that cannot be found or loaded is a 404."""
    try:
        return await service.form_target(row_id)
    except ValueError as exc:
        raise PanelError(str(exc), status=404) from exc


async def _describe_picker(ctx: Any) -> dict[str, Any]:
    service = _service()
    rows = service.rows(await mcp_runtime(ctx))
    installed = sum(is_installed(row) for row in rows)
    return {
        "panel": "apps",
        "kind": "picker",
        "title": "Apps",
        "summary": [{"label": "Installed", "value": str(installed)}],
        "sections": service.sections(rows),
        "rows": rows,
        "row_actions": ROW_ACTIONS,
        "toolbar_actions": TOOLBAR_ACTIONS,
    }


def _inputs(row: dict[str, Any], app: dict[str, Any] | None) -> list[dict[str, Any]]:
    if app is None or row["kind"] != "mcp":
        return []
    return list(app["mcp"].get("inputs", []))


def _from_registry(row: dict[str, Any]) -> bool:
    return row["id"].startswith("registry:")


def _fill(text: str, service: AppsService) -> str:
    builtins = {"workspace": str(service.workspace), "home": str(service.home)}
    return TOKEN_RE.sub(lambda m: builtins.get(m.group(1), m.group(0)), text)


def _install_form(service: AppsService, row: dict[str, Any], app: dict[str, Any] | None) -> dict[str, Any]:
    inputs = _inputs(row, app)
    if not inputs and not _from_registry(row):
        raise PanelError(f"{row['label']} has no inputs to fill in", status=404)
    sections: list[dict[str, Any]] = []
    context: dict[str, Any] = {"id": row["id"]}
    if _from_registry(row):
        if app is None:
            raise PanelError(f"{row['label']} is already {row['state']}", status=404)
        # Kollab has not reviewed it: the command shows before Add saves and starts it, and Add
        # saves only that command (the fingerprint).
        help_text = f"{NOT_REVIEWED} Add saves it and starts it in this session."
        runs = make_field("_runs", "label", "Runs", help=help_text, value=row["preview"])
        sections.append({"id": "command", "title": "Command", "fields": [runs]})
        context["fingerprint"] = fingerprint(app)
    fields = [
        make_field(
            inp["name"],
            "text_input",
            inp.get("label", inp["name"]),
            help=inp.get("help", ""),
            value=_fill(inp["default"], service) if inp.get("default") else None,
            secret=bool(inp.get("secret")),
        )
        for inp in inputs
    ]
    if fields:
        sections.append({"id": "s0", "title": "Settings", "fields": fields})
    return {
        "panel": "apps-install",
        "kind": "form",
        "title": f"Add {row['label']}",
        "sections": sections,
        "actions": [{"id": "install", "label": "Add"}],
        "context": context,
    }


async def _run(
    service: AppsService,
    action: str,
    row_id: str,
    values: dict[str, str] | None = None,
    confirm: str | None = None,
) -> dict[str, Any]:
    """One service action. A refusal becomes an ok=false result, never an exception."""
    try:
        if action == "add":
            return await service.add(row_id, values, confirm)
        if action == "enable":
            return await service.enable(row_id)
        return await service.remove(row_id)
    except (ValueError, KeyError, OSError) as exc:
        return {"ok": False, "message": str(exc), "mcp_changed": False}


def _outcome(result: dict[str, Any], panel: dict[str, Any]) -> dict[str, Any]:
    if result["ok"]:
        return ok_result(result["message"], panel=panel)
    return error_result(result["message"], panel=panel)


class AppsPanel:
    """The Apps picker (``/mcp`` in the web UI). Rows, sections, and the Add, Turn On and Remove row actions."""

    name = "apps"
    kind = "picker"

    async def describe(self, ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        return await _describe_picker(ctx)

    async def act(self, ctx: Any, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "refresh":
            return ok_result("Refreshed.", panel=await _describe_picker(ctx))
        if action not in {row_action["id"] for row_action in ROW_ACTIONS}:
            raise unknown_action(self.name, action)
        service = _service()
        row_id = str(payload.get("id") or "")
        row, app = await _target(service, row_id)
        if action not in row["actions"]:
            raise PanelError(f"{row['label']} has no '{action}' action", status=404)
        if action == "add" and (_from_registry(row) or required_unset(_inputs(row, app), {})):
            return ok_result(open=_install_form(service, row, app), panel=await _describe_picker(ctx))
        result = await _run(service, action, row_id)
        await apply_change(ctx, result)
        return _outcome(result, await _describe_picker(ctx))


class AppsRegistryPanel:
    """``apps-registry``: MCP Registry servers for a search. Their actions go through ``apps``."""

    name = "apps-registry"
    kind = "picker"

    async def describe(self, ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        found = await _service().registry_rows(str(params.get("query") or ""), await mcp_runtime(ctx))
        return {
            "panel": "apps-registry",
            "kind": "picker",
            "title": "MCP Registry",
            "summary": [],
            "sections": [{"id": "registry", "title": "MCP Registry", "ids": [row["id"] for row in found["rows"]]}],
            "rows": found["rows"],
            "row_actions": ROW_ACTIONS,
            "toolbar_actions": [],
            "query": found["query"],
            "note": found["note"],
        }

    async def act(self, ctx: Any, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise unknown_action(self.name, action)


class AppsInstallPanel:
    """``apps-install``: confirm one MCP server and fill in what it needs before Add writes it."""

    name = "apps-install"
    kind = "form"

    async def describe(self, ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        service = _service()
        row, app = await _target(service, str(params.get("id") or ""))
        return _install_form(service, row, app)

    async def act(self, ctx: Any, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action != "install":
            raise unknown_action(self.name, action)
        service = _service()
        context = payload.get("context") or {}
        row_id = str(context.get("id") or "")
        row, app = await _target(service, row_id)
        form = _install_form(service, row, app)
        confirm = str(context.get("fingerprint") or "") or None
        if _from_registry(row) and confirm != form["context"]["fingerprint"]:
            return error_result(
                "This server changed in the MCP Registry. Check the command again, then Add.", panel=form
            )
        inputs = _inputs(row, app)
        names = {inp["name"] for inp in inputs}
        changes = payload.get("changes") or {}
        # A cleared field arrives as "" and stays cleared: the default does not come back.
        values = {str(name): str(value) for name, value in changes.items() if value is not None and str(name) in names}
        missing = required_unset(inputs, values)
        if missing:
            errors = {inp["name"]: f"{inp.get('label', inp['name'])} is required" for inp in missing}
            return error_result("Fill in the required fields.", errors=errors, panel=form)
        result = await _run(service, "add", row_id, values, confirm)
        await apply_change(ctx, result)
        if result["ok"]:
            return ok_result(result["message"], panel=await _describe_picker(ctx))
        return error_result(result["message"], panel=form)


PANELS = {
    AppsPanel.name: AppsPanel(),
    AppsRegistryPanel.name: AppsRegistryPanel(),
    AppsInstallPanel.name: AppsInstallPanel(),
}
