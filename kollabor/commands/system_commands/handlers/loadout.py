"""Loadout command handler -- polished replacement for the `/profile` picker.

A **loadout** is a named preset: provider profile + model + params
(temperature, effort, max_tokens). The data model and persistence
(``LoadoutManager`` / ``Loadout``) live in ``kollabor_ai.loadout_manager``,
built by another agent in parallel -- everything here is lazy-imported so
this module ``py_compile``s cleanly whether or not that module has landed.

`/llm` pushes ``LoadoutListAltView``; `/llm new` pushes
``LoadoutFormAltView`` directly, seeded from the active profile; `/llm
<name>` resolves and activates with no UI at all. List and form are always
pushed as separate, sequential top-level AltViews (never one nested inside
the other) -- see ``plugins/altview/loadout_altview.py``'s module docstring
for why. Esc from the form re-opens a fresh list; Enter on a list row or a
successful form save ends the whole flow with a chat message, mirroring how
`/setup` hands control back after its wizard closes.
"""

import logging
from typing import Any, Optional

from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    CommandResult,
    SlashCommand,
    SubcommandInfo,
)

from ..base import BaseCommandHandler

logger = logging.getLogger(__name__)

# Generous bound on list<->form round-trips per /llm invocation -- a
# backstop against a runaway loop bug, not a real usage limit.
_MAX_ROUNDTRIPS = 25


class LoadoutCommandHandler(BaseCommandHandler):
    """Handles `/llm` -- browse, create, edit, and switch model loadouts."""

    MODAL_ACTIONS: set[str] = set()

    def __init__(
        self,
        command_registry,
        event_bus,
        profile_manager=None,
        llm_service=None,
    ):
        super().__init__(command_registry, event_bus)
        self._profile_manager = profile_manager
        self._llm_service_override = llm_service

    @property
    def profile_manager(self):
        if self._profile_manager is not None:
            return self._profile_manager
        return self.event_bus.get_service("profile_manager")

    @property
    def llm_service(self):
        if self._llm_service_override is not None:
            return self._llm_service_override
        return self.event_bus.get_service("llm_service")

    @property
    def renderer(self):
        return self.event_bus.get_service("renderer")

    def register_commands(self) -> None:
        loadout_command = CommandDefinition(
            name="llm",
            description="Model loadouts — switch provider/model/param presets",
            handler=self.handle_loadout,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.INSTANT,
            aliases=["loadout", "ld"],
            icon="[LOAD]",
            subcommands=[
                SubcommandInfo("", "", "Open the loadout picker"),
                SubcommandInfo("new", "", "Create a loadout"),
                SubcommandInfo("<name>", "", "Activate a loadout by name"),
            ],
        )
        self.command_registry.register_command(loadout_command)

    async def handle_loadout(self, command: SlashCommand) -> CommandResult:
        """Route `/llm`, `/llm new`, and `/llm <name>`."""
        args = command.args or []
        if not args:
            return await self._open_list()
        if args[0].lower() == "new":
            return await self._open_new()
        return await self._activate_by_name(" ".join(args))

    # -- manager / stack manager construction --------------------------------

    def _get_manager(self) -> Optional[Any]:
        """Lazily construct the LoadoutManager (core lands separately)."""
        pm = self.profile_manager
        if pm is None:
            return None
        try:
            from kollabor_ai.loadout_manager import LoadoutManager
        except Exception as exc:  # pragma: no cover - import guard
            logger.error("Loadout manager unavailable: %s", exc)
            return None
        try:
            return LoadoutManager(pm, config=self.config_manager)
        except Exception as exc:
            logger.error("Failed to construct LoadoutManager: %s", exc)
            return None

    def _get_stack_manager(self):
        """Resolve the AltView stack manager, creating it lazily if needed."""
        stack_mgr = self.event_bus.get_service("altview_stack_manager")
        if stack_mgr is not None:
            return stack_mgr
        try:
            from kollabor_tui.altview.stack_manager import AltViewStackManager

            stack_mgr = AltViewStackManager(self.event_bus, self.renderer)
            self.event_bus.register_service("altview_stack_manager", stack_mgr)
            return stack_mgr
        except Exception as exc:
            logger.error("Failed to create AltView stack manager: %s", exc)
            return None

    @staticmethod
    def _activated_message(loadout: Any, verb: str = "active") -> str:
        if loadout is None:
            return f"loadout {verb}."
        return f"Loadout '{loadout.name}' {verb} — {loadout.model} via {loadout.provider_profile}"

    # -- list / form orchestration --------------------------------------

    async def _open_list(self) -> CommandResult:
        try:
            from plugins.altview.loadout_altview import LoadoutListAltView
        except Exception as exc:  # pragma: no cover - import guard
            logger.error("Loadout picker unavailable: %s", exc)
            return CommandResult(
                success=False,
                message=f"Loadout picker unavailable: {exc}",
                display_type="error",
            )

        stack_mgr = self._get_stack_manager()
        if stack_mgr is None:
            return CommandResult(
                success=False,
                message="Loadout UI unavailable (no AltView stack manager).",
                display_type="error",
            )

        manager = self._get_manager()
        if manager is None:
            return CommandResult(
                success=False,
                message="Loadout manager unavailable — configure a provider with /setup.",
                display_type="error",
            )

        for _ in range(_MAX_ROUNDTRIPS):
            view = LoadoutListAltView()
            view.set_context(
                manager=manager,
                profile_manager=self.profile_manager,
                event_bus=self.event_bus,
            )
            # reuse=False: one-shot flow -- always run the fresh view we
            # built and read its own result flags below.
            await stack_mgr.push(view, "loadout-list", reuse=False)

            if getattr(view, "result_activated", False):
                return CommandResult(
                    success=True,
                    message=self._activated_message(view.result_loadout),
                    display_type="success",
                )

            if getattr(view, "result_open_form", False):
                form_result = await self._run_form(
                    stack_mgr,
                    manager,
                    mode=view.result_form_mode,
                    base=view.result_form_base,
                    name_style=view.result_form_name_style,
                )
                if form_result is not None:
                    return form_result
                continue  # form cancelled (Esc) -- back to a fresh list

            return CommandResult(
                success=True,
                message="loadout cancelled — no changes made.",
                display_type="info",
            )

        return CommandResult(
            success=False,
            message="loadout picker exited unexpectedly.",
            display_type="error",
        )

    async def _run_form(
        self,
        stack_mgr,
        manager,
        mode: str,
        base: Any,
        name_style: str,
    ) -> Optional[CommandResult]:
        """Push the form once. Returns a CommandResult on save, else None."""
        from plugins.altview.loadout_altview import LoadoutFormAltView

        form = LoadoutFormAltView()
        form.set_context(
            manager=manager,
            profile_manager=self.profile_manager,
            event_bus=self.event_bus,
            mode=mode,
            base=base,
            name_style=name_style,
        )
        await stack_mgr.push(form, "loadout-form", reuse=False)

        if not getattr(form, "result_saved", False):
            return None  # Esc -- caller re-opens the list

        loadout = form.result_loadout
        verb = "updated and active" if form.result_activated else "updated"
        if mode != "edit":
            verb = "saved and active" if form.result_activated else "saved"
        return CommandResult(
            success=True,
            message=self._activated_message(loadout, verb=verb),
            display_type="success",
        )

    async def _open_new(self) -> CommandResult:
        manager = self._get_manager()
        if manager is None:
            return CommandResult(
                success=False,
                message="Loadout manager unavailable — configure a provider with /setup.",
                display_type="error",
            )

        from plugins.altview.loadout_altview import build_active_profile_base

        base = build_active_profile_base(self.profile_manager)
        if base is None:
            return CommandResult(
                success=False,
                message="No active model to base a new loadout on — configure a provider with /setup.",
                display_type="error",
            )

        stack_mgr = self._get_stack_manager()
        if stack_mgr is None:
            return CommandResult(
                success=False,
                message="Loadout UI unavailable (no AltView stack manager).",
                display_type="error",
            )

        result = await self._run_form(
            stack_mgr, manager, mode="create", base=base, name_style="plain"
        )
        if result is not None:
            return result
        # Cancelled (Esc) -- don't strand the user, show the picker instead.
        return await self._open_list()

    # -- direct activation (no UI) ----------------------------------------

    async def _activate_by_name(self, query: str) -> CommandResult:
        manager = self._get_manager()
        if manager is None:
            return CommandResult(
                success=False,
                message="Loadout manager unavailable — configure a provider with /setup.",
                display_type="error",
            )

        try:
            loadout, suggestions = manager.resolve(query)
        except Exception as exc:
            logger.error("Loadout resolve('%s') failed: %s", query, exc)
            return CommandResult(
                success=False,
                message=f"Could not resolve loadout '{query}': {exc}",
                display_type="error",
            )

        if loadout is None:
            message = f"No loadout matching '{query}'."
            if suggestions:
                message += "\n  did you mean:\n    " + "\n    ".join(suggestions)
            return CommandResult(success=False, message=message, display_type="error")

        try:
            activated = await manager.activate(loadout, self.event_bus)
        except Exception as exc:
            logger.error("Loadout activate('%s') failed: %s", loadout.name, exc)
            return CommandResult(
                success=False,
                message=f"Could not activate loadout '{loadout.name}': {exc}",
                display_type="error",
            )

        if not activated:
            return CommandResult(
                success=False,
                message=f"Could not activate loadout '{loadout.name}'.",
                display_type="error",
            )

        return CommandResult(
            success=True,
            message=self._activated_message(activated),
            display_type="success",
        )
