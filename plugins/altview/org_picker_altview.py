"""Interactive team and roster selector for ``/hub org``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress
from plugins.hub.rosters import LibraryError, Team, effective_team, team_launch_order


@dataclass(frozen=True)
class OrgSelection:
    """Selection returned by the one-shot organization picker."""

    team: Team
    members: tuple[str, ...]
    lead: str
    mission: str


class OrgPickerAltView(AltView):
    """Choose a team, toggle roster members, set its lead, and enter a mission.

    When ``team`` is supplied, the view opens directly on that team's roster.
    With no team it first displays the available teams, then the roster.
    Controls: arrows navigate; Enter selects/launches; Space toggles a member;
    L sets the highlighted member as lead; M edits the mission; Escape cancels.
    """

    def __init__(
        self,
        teams: Sequence[Team],
        *,
        team: Team | None = None,
        mission: str = "",
    ) -> None:
        super().__init__(
            AltViewMetadata(
                plugin_type="hub-org-picker",
                description="Select a team and its launch roster",
                version="1.0.0",
                author="Kollabor",
                category="internal",
                icon="[ORG]",
                aliases=[],
                supports_named_sessions=False,
                supports_background=False,
            )
        )
        self.teams = list(teams)
        self.team: Team | None = None
        self.phase = "teams"
        self.selected_index = 0
        self.scroll_offset = 0
        self.selected_members: set[str] = set()
        self.lead = ""
        self.mission = mission
        self._mission_before_edit = mission
        self._editing_mission = False
        self._mission_buffer = mission
        self._message = ""
        self.result: OrgSelection | None = None

        if team is not None:
            self._select_team(team)
        elif not self.teams:
            self._message = "No teams are available in this folder."

    def _select_team(self, team: Team) -> None:
        self.team = team
        self.phase = "members"
        self.selected_members = {member.persona.name for member in team.members}
        self.lead = team.lead
        member_names = [member.persona.name for member in team_launch_order(team)]
        self.selected_index = member_names.index(team.lead) if team.lead in member_names else 0
        self.scroll_offset = 0
        self._message = ""

    def _member_names(self) -> list[str]:
        if self.team is None:
            return []
        return [member.persona.name for member in team_launch_order(self.team)]

    def _effective_team(self) -> Team | None:
        if self.team is None:
            return None
        try:
            selected, _excluded = effective_team(
                self.team,
                members=[
                    name for name in self._member_names() if name in self.selected_members
                ],
                lead=self.lead,
            )
            return selected
        except LibraryError:
            return None

    def _member_rows(self) -> list[tuple[str, int, str, str, bool, bool]]:
        """Return member rows with selected members shown on the effective tree."""
        if self.team is None:
            return []
        effective = self._effective_team()
        effective_by_name = (
            {member.persona.name: member for member in effective.members}
            if effective
            else {}
        )
        authored_by_name = {
            member.persona.name: member for member in self.team.members
        }
        effective_parents = {
            name: member.reports_to for name, member in effective_by_name.items()
        }
        rows = []
        for member in team_launch_order(self.team):
            name = member.persona.name
            selected = name in self.selected_members
            parent = (
                effective_parents.get(name)
                if selected
                else authored_by_name[name].reports_to
            )
            parent_map = {
                item.persona.name: (
                    effective_parents.get(item.persona.name, item.reports_to)
                    if item.persona.name in self.selected_members
                    else item.reports_to
                )
                for item in self.team.members
            }
            depth = 0
            seen: set[str] = set()
            current_parent = parent
            while current_parent and current_parent in parent_map:
                if current_parent in seen:
                    break
                seen.add(current_parent)
                depth += 1
                current_parent = parent_map[current_parent]
            rows.append(
                (
                    name,
                    depth,
                    member.role,
                    parent or "",
                    selected,
                    name == self.lead,
                )
            )
        return rows

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self.result = None
        self._editing_mission = False
        self._mission_buffer = self.mission
        self._message = ""

    async def render_frame(self, delta_time: float) -> bool:
        if self.renderer is None:
            return False
        width, height = self.renderer.get_terminal_size()
        theme = T()
        row = 0

        def write_line(text: str, *, selected: bool = False, footer: bool = False) -> None:
            nonlocal row
            bg = theme.dark[1] if footer else (theme.primary[0] if selected else theme.dark[0])
            fg = theme.text_dim if footer else (theme.text_dark if selected else theme.text)
            self.renderer.write_at(
                0,
                row,
                solid(text[:width].ljust(width), bg, fg, width),
                "",
            )
            row += 1

        self.renderer.write_at(0, row, solid_fg(C["half_bottom"] * width, theme.primary[0]), "")
        row += 1
        if self.phase == "teams":
            write_line("  SELECT TEAM", selected=True)
            write_line("  Up/Down: navigate | Enter: choose | Esc: cancel")
            content = [
                f"  {item.name}  [{item.source}]  {len(item.members)} members"
                for item in self.teams
            ]
            selected_index = self.selected_index
        else:
            assert self.team is not None
            title = f"  {self.team.name}  |  lead: {self.lead or '(none)'}"
            write_line(title, selected=True)
            if self._editing_mission:
                write_line(f"  Mission: {self._mission_buffer}_")
                write_line("  Enter: save mission | Esc: discard edit")
            else:
                write_line(f"  Mission: {self.mission or '(none; press M to add)'}")
                write_line("  Space: toggle | L: set lead | M: mission | Enter: launch | Esc: cancel")
            content = []
            for name, depth, role, parent, selected, is_lead in self._member_rows():
                marker = "x" if selected else " "
                parent_text = f" <- {parent}" if parent else ""
                lead_text = "  [lead]" if is_lead else ""
                content.append(
                    f"  [{marker}] {'  ' * depth}{name}: {role}{parent_text}{lead_text}"
                )
            selected_index = self.selected_index

        list_height = max(0, height - row - 3)
        total = len(content)
        if total and selected_index < self.scroll_offset:
            self.scroll_offset = selected_index
        elif total and selected_index >= self.scroll_offset + list_height:
            self.scroll_offset = selected_index - list_height + 1
        visible = content[self.scroll_offset : self.scroll_offset + list_height]
        for idx, line in enumerate(visible, start=self.scroll_offset):
            write_line(line, selected=idx == selected_index)
        for _ in range(max(0, list_height - len(visible))):
            write_line("")
        count = len(self.selected_members)
        status = self._message or f"  {count} selected"
        if not self._message and self.team is not None:
            status += f" | {self.team.source}"
        write_line(f"  {status}" if not status.startswith("  ") else status)
        self.renderer.write_at(0, row, solid_fg(C["half_bottom"] * width, theme.dark[1]), "")
        return True

    async def handle_input(self, key_press: KeyPress) -> bool:
        name = key_press.name or ""
        char = key_press.char or ""

        if self._editing_mission:
            if name == "Escape":
                self._mission_buffer = self._mission_before_edit
                self._editing_mission = False
                return False
            if name == "Enter":
                self.mission = self._mission_buffer.strip()
                self._editing_mission = False
                return False
            if name in ("Backspace", "Delete"):
                self._mission_buffer = self._mission_buffer[:-1]
            elif char and char.isprintable() and len(char) == 1:
                self._mission_buffer += char
            return False

        if name == "Escape":
            self.result = None
            return True

        if self.phase == "teams":
            if name in ("ArrowUp", "Up"):
                self.selected_index = max(0, self.selected_index - 1)
            elif name in ("ArrowDown", "Down"):
                self.selected_index = min(len(self.teams) - 1, self.selected_index + 1)
            elif name == "Enter" and self.teams:
                self._select_team(self.teams[self.selected_index])
            return False

        names = self._member_names()
        if not names:
            self._message = "This team has no members."
            return False
        if name in ("ArrowUp", "Up"):
            self.selected_index = max(0, self.selected_index - 1)
            self._message = ""
        elif name in ("ArrowDown", "Down"):
            self.selected_index = min(len(names) - 1, self.selected_index + 1)
            self._message = ""
        elif char == " " or name == "Space":
            member = names[self.selected_index]
            if member == self.lead:
                self._message = f"Set a replacement lead with L before removing {member}."
            elif member in self.selected_members:
                self.selected_members.remove(member)
                self._message = ""
            else:
                self.selected_members.add(member)
                self._message = ""
        elif char.lower() == "l":
            member = names[self.selected_index]
            self.selected_members.add(member)
            self.lead = member
            self._message = f"{member} is now the lead."
        elif char.lower() == "m":
            self._mission_before_edit = self.mission
            self._mission_buffer = self.mission
            self._editing_mission = True
            self._message = ""
        elif name == "Enter":
            effective = self._effective_team()
            if effective is None:
                self._message = "Keep the lead selected or choose a replacement with L."
                return False
            selected = tuple(
                member.persona.name
                for member in team_launch_order(self.team)
                if member.persona.name in self.selected_members
            )
            self.result = OrgSelection(
                team=self.team,
                members=selected,
                lead=self.lead,
                mission=self.mission,
            )
            return True
        return False
