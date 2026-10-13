"""Terminal ``/apps``: the Apps directory as text. ``list``, ``search``, ``add`` and ``remove``.

Same service and same MCP reload as the Apps panel. An MCP add that needs a
secret is saved off, and the reply says how to set the key. ``search`` also
lists MCP Registry servers. ``add registry:<name>`` shows what one runs and a
code; ``add registry:<name> <code>`` adds exactly that.
"""

import asyncio

from kollabor.apps.registry import NOT_REVIEWED, fingerprint
from kollabor.apps.service import AppsService, required_unset
from kollabor.panels.apps import apply_change, mcp_runtime
from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandResult,
    SlashCommand,
    SubcommandInfo,
)

from ..base import BaseCommandHandler

USAGE = "usage: /apps [list | search <words> | add <id> | remove <id>]"
KIND_IDS = {"mcp", "cli", "skill"}
LIBRARY_IDS = {"your-skills", "built-in"}  # skills already in the library: counted, not listed
LIST_ROWS = 4  # rows per kind in /apps list, so the list fits a terminal; search shows every match
# A search prints after this long at most. A slower registry search keeps going and fills the
# registry cache, so the same search a moment later answers at once.
REGISTRY_WAIT_SECONDS = 10
_searches: set[asyncio.Future] = set()  # running registry searches: the loop keeps only weak references


def _service() -> AppsService:
    return AppsService()


def _line(row: dict) -> str:
    note = f" - {row['note']}" if row.get("note") else ""  # why it is unavailable, what Add does first
    return f"  [{row['state']}] {row['label']} ({row['id']}){note}"


def _library_only(row: dict) -> bool:
    return any(badge in ("Built-in", "Local") for badge in row["badges"])


def _listing(service: AppsService, rows: list[dict], limit: int | None = None) -> str:
    """Sections and rows. A list (limit set) shows the kind sections, catalog rows only, and counts the rest."""
    by_id = {row["id"]: row for row in rows}
    lines: list[str] = []
    for section in service.sections(rows):
        if limit is None:
            shown = section["ids"]
        elif section["id"] in KIND_IDS:
            shown = [row_id for row_id in section["ids"] if not _library_only(by_id[row_id])][:limit]
        elif section["id"] in LIBRARY_IDS:
            count = len(section["ids"])
            lines.append(section["title"])
            lines.append(f"  {count} skill{'' if count == 1 else 's'}. /apps search <words> finds them.")
            continue
        else:
            continue
        lines.append(section["title"])
        lines.extend(_line(by_id[row_id]) for row_id in shown)
        hidden = len(section["ids"]) - len(shown)
        if hidden:
            lines.append(f"  … {hidden} more. /apps search <words> finds them.")
    return "\n".join(lines)


def _registry_listing(found: dict) -> str:
    """MCP Registry hits. The "not reviewed" note is said once, not on every line."""
    lines = ["MCP Registry"]
    lines += [_line({**row, "note": "" if row["note"] == NOT_REVIEWED else row["note"]}) for row in found["rows"]]
    if found["note"]:
        lines.append(f"  {found['note']}")
    if any(row["state"] == "available" for row in found["rows"]):
        lines.append("  Kollab has not reviewed these servers. /apps add <id> shows what one runs.")
    return "\n".join(lines)


def _registry_confirm(row: dict, app: dict | None) -> str:
    """What a registry server runs, and the code that adds exactly that. Nothing is saved yet."""
    if app is None or row["state"] != "available":
        return _line(row)
    inputs = app["mcp"]["inputs"]
    lines = [f"{row['label']} ({row['id']})", f"  {NOT_REVIEWED}", f"  Runs: {row['preview']}"]
    defaults = [f"{inp['name']}={inp['default']}" for inp in inputs if inp.get("default") and not inp.get("secret")]
    if defaults:
        lines.append(f"  With: {' '.join(defaults)}")
    unset = required_unset(inputs, {})
    needs = [inp["name"] for inp in unset if not inp.get("secret")]
    if needs:
        lines.append(f"  It needs {', '.join(needs)}, which /apps can't ask for. Add it from Apps in the web UI.")
        return "\n".join(lines)
    secrets = [inp["name"] for inp in unset if inp.get("secret")]
    if secrets:
        lines.append(f"  Add saves it off. Set {', '.join(secrets)} with /mcp setup, then turn it on.")
    lines.append(f"  To add it: /apps add {row['id']} {fingerprint(app)}")
    return "\n".join(lines)


def _matches(row: dict, words: list[str]) -> bool:
    haystack = " ".join([row["label"], row["detail"], row["publisher"], *row["tags"]]).casefold()
    return all(word in haystack for word in words)


class AppsCommandHandler(BaseCommandHandler):
    """Handle the ``/apps`` text commands."""

    def register_commands(self) -> None:
        self.command_registry.register_command(
            CommandDefinition(
                name="apps",
                description="Browse, add and remove MCP servers, CLI tools and skills",
                category=CommandCategory.SYSTEM,
                plugin_name="system",
                handler=self.handle_apps,
                subcommands=[
                    SubcommandInfo("list", "", "Show every app and its state"),
                    SubcommandInfo("search", "<words>", "Find apps, and servers in the MCP Registry"),
                    SubcommandInfo("add", "<id>", "Add an app, e.g. mcp:context7, or show a registry server"),
                    SubcommandInfo("remove", "<id>", "Remove an MCP server or a marketplace skill"),
                ],
            )
        )

    def _state_service(self):
        if self.event_bus is None or not hasattr(self.event_bus, "get_service"):
            return None
        return self.event_bus.get_service("state_service")

    async def handle_apps(self, command: SlashCommand) -> CommandResult:
        args = command.args or []
        verb = args[0].lower() if args else "list"
        rest = args[1:]
        service = _service()
        state = self._state_service()

        if verb == "list" and not rest:
            rows = service.rows(await mcp_runtime(state))
            return CommandResult(success=True, message=_listing(service, rows, limit=LIST_ROWS))

        if verb == "search" and rest:
            words = [word.casefold() for word in rest]
            runtime = await mcp_runtime(state)
            rows = [row for row in service.rows(runtime) if _matches(row, words)]
            directory = _listing(service, rows) if rows else f"No apps in the directory match '{' '.join(rest)}'."
            search = asyncio.ensure_future(service.registry_rows(" ".join(rest), runtime))
            _searches.add(search)
            search.add_done_callback(_searches.discard)
            done, _ = await asyncio.wait({search}, timeout=REGISTRY_WAIT_SECONDS)
            if done:
                found = _registry_listing(search.result())
            else:
                found = (
                    "MCP Registry\n  Still searching: the MCP Registry is slow right now. Run this search again soon."
                )
            return CommandResult(success=True, message=f"{directory}\n{found}")

        registry_id = verb == "add" and bool(rest) and rest[0].startswith("registry:")
        if registry_id and len(rest) == 1:
            try:
                row, app = await service.form_target(rest[0])
            except ValueError as exc:
                return CommandResult(success=False, message=str(exc), display_type="error")
            return CommandResult(success=True, message=_registry_confirm(row, app))

        if verb in ("add", "remove") and (len(rest) == 1 or (registry_id and len(rest) == 2)):
            try:
                if verb == "add":
                    result = await service.add(rest[0], confirm=rest[1] if len(rest) == 2 else None)
                else:
                    result = await service.remove(rest[0])
            except (ValueError, KeyError, OSError) as exc:
                return CommandResult(success=False, message=str(exc), display_type="error")
            await apply_change(state, result)
            return CommandResult(
                success=result["ok"],
                message=result["message"],
                display_type="success" if result["ok"] else "error",
            )

        return CommandResult(success=False, message=USAGE, display_type="error")
