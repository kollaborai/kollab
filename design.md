# Kollab Web UI Design

This is the living guide for the Web UI aesthetic. It records the direction from
Marco's ongoing UI review. Keep updating it as decisions change; the review is
still in progress, and existing screens may need further cleanup.

## Direction

Keep the interface compact, quiet, and useful. Use the app's existing system font,
neutral palette, shared controls, and restrained rounded surfaces. Make the
content, agent identity, and available actions easy to read.

## Confirmed Decisions

- Remove decorative borders. Separate areas with spacing and changes in shade.
- Match the typography and density used by the existing settings and dialogs.
- Use plain topic navigation without numbered badges.
- Remove decorative heading icons, sparkles, uppercase taglines, and lesson counters.
- Keep Learn as a book icon in the sidebar's bottom action row.
- Open Learn in a compact modal like Apps, with an X in the top right and scrolling
  inside the modal. Keep it focused on topics. Remove the title bar,
  completion controls, progress tracking, and Previous/Next footer.
- Give What's New its own page and newspaper icon in the bottom action row.
- Match the main pane's outer corners and inset spacing to the floating sidebar.
- Keep the removed guide links out of the sidebar and Agents modal.
- Use Overview, Access, and Knocks tabs inside Network. Keep Trust as a compact
  single-choice control and device name in a short row with an explicit Save.
  Show the owning connection's Knocks address as plain text with a copy icon
  when available; show Unavailable when the owner has no address to report.
- Reorder agents by dragging, with neighboring rows moving during the gesture.
- Reorder remote devices within their network by dragging the device header.
  Move the whole group together and save the order with the agent ordering preferences.
- Hide an agent by dragging it outside the sidebar. On release, shrink it into
  the bottom Hidden Agents button. Allow restoration from that button.

## Surfaces and Color

Use the shared semantic tokens in
[styles.css](packages/kollabor-webui/frontend/src/styles.css).
The current dark palette separates the page, chrome, panels, and popovers by tone:

- Page: `--background`, currently `oklch(0.145 0 0)`.
- Sidebar and chrome: `--sidebar`, currently `oklch(0.18 0 0)`.
- Panels: `--card`, currently `oklch(0.205 0 0)`.
- Popovers: `--popover`, currently `oklch(0.269 0 0)`.

Use `text-foreground` for primary content and `text-muted-foreground` for supporting
details. Use semantic colors for meaningful state. Reuse the corresponding light
theme tokens when the app is in light mode.

Keep structural borders transparent, including the sidebar border. Avoid framing
every card, header, section, or selected row with a line. Use a soft active fill
and clear hover feedback. Keep keyboard focus visible.

Prefer simple tonal surfaces and restrained shadows. Extra gradients, glows, and
ornaments need a clear purpose in the current task.

## Typography

Inherit the app's system sans-serif stack. Use the existing rem-based utilities:

- Page, dialog, and topic titles: `text-lg font-semibold`.
- Body and controls: `text-sm`; section headings: `text-sm font-semibold`.
- Secondary labels, metadata, and supporting details: `text-xs`.

At a 16px root these sizes are 18px, 14px, and 12px. The current fine-pointer rule
sets the root to 90%, producing 16.2px, 12.6px, and 10.8px. Preserve this shared
density behavior instead of forcing individual pages to a separate pixel scale.
Keep the existing 16px rule for narrow-screen composer inputs.

Use normal body line spacing and modest heading weight. Keep page titles at the
app's panel scale. Avoid oversized hero text, widely tracked uppercase eyebrows,
and extra branding above ordinary content.

## Spacing and Shape

Reuse the spacing of neighboring screens. Common starting points are `px-4 py-3`
for headers, `p-4` through `p-6` for content groups, `gap-4` or `gap-5` between
sections, and `gap-1.5` or `gap-2` within controls. Adapt padding at narrow widths.

Use the existing corner treatments: `rounded-md` for controls and the established
`rounded-xl` or `rounded-2xl` surfaces where grouping helps. Keep repeated rows
aligned and similar controls the same height. Preserve usable touch targets and
enough room for text to wrap.

## Icons, Labels, and Navigation

Use the existing icon set for actions, destinations, and real state. Keep the
established logo and agent avatars consistent with their shared components.
Give icon-only controls accessible names and tooltips.

Keep headings plain. The removed gem header badge, sparkles, book heading badges,
"THE KOLLAB FIELD GUIDE" tagline, and "LESSON X OF Y" label should stay removed.

Topic menus use text rows with a subdued selected background. Use numbering for
instructions whose order matters. Learn has no completion state or progress counter.

Keep the sidebar focused on agents, workspaces, networks, and its bottom utility
actions. Do not reintroduce the agents/workspaces guide links, the Network guides
section, or the five Learn links removed from the Agents modal.

Use short, concrete labels and keep casing consistent with neighboring controls.
Keep secondary and advanced actions subordinate to the main task.

## Controls and Feedback

Reuse the shared Button, Tabs, Dialog, Sheet, Popover, and sidebar components.
The current Button `outline` variant already uses a tonal background; its name
does not call for a visible stroke. Check the rendered control before adding
styling overrides.

Keep selected, hover, focus, disabled, loading, and error states readable. Show
status and counts from real state. Give errors a useful explanation and recovery
action where one exists.

## Motion and Agent Gestures

Use motion to explain a change of state or position. Keep it short and respect
reduced motion.

- During reordering, show the grabbed row and move neighboring rows aside in real
  time. Keep the workspace, device, and network scopes intact.
- During drag-out hiding, show "Release to hide" after the row passes the sidebar
  edge. The current implementation leaves a 32px gutter for small sideways slips.
- On release, shrink the row into the Hidden Agents button. The current travel
  takes about 220ms. Keep the footer destination available when the list is empty.
- Escape cancels. Dragging back inside cancels the removal cue. Preserve keyboard
  reordering and the menu's Hide Agent action.
- Hiding removes a sidebar entry and remains reversible. It does not stop an agent
  or delete its session. Restore the row and show the error if saving fails.

## Working References

The learning system remains under review. The first reference-browser and task-card
mockups were rejected. Current prototypes explore a session walkthrough using the
actual controls, and an interactive model of networks, devices, workspaces, and
agents. Neither content direction has been selected for implementation.

- [LearnPage.tsx](packages/kollabor-webui/frontend/src/components/learn/LearnPage.tsx):
  compact headings, plain topic navigation, and borderless content groups.
- [AgentLibrary.tsx](packages/kollabor-webui/frontend/src/components/agents/AgentLibrary.tsx):
  Agents dialog typography and the cleaned-up header.
- [AppSidebar.tsx](packages/kollabor-webui/frontend/src/components/shell/AppSidebar.tsx):
  bottom utility actions, grouping, and Hidden Agents restoration.
- [AgentSidebarRow.tsx](packages/kollabor-webui/frontend/src/components/shell/AgentSidebarRow.tsx):
  drag feedback, cancellation, and the hide animation.
- [Shared controls](packages/kollabor-webui/frontend/src/components/ui/) and
  [PanelHost.tsx](packages/kollabor-webui/frontend/src/components/panels/PanelHost.tsx):
  existing controls and panel structure.

These are implementation references. Apply the decisions above when a remaining
detail in an existing screen still needs cleanup.

## Review While Editing

Check the changed screen and sibling surfaces at desktop, 820px, and 390px.
Check text size, wrapping, overflow, focus, and the states the change affects.
Exercise gestures and restoration in the running UI. A narrow layout check does
not establish touch-input behavior; record any input path that remains unverified.

Keep this guide current as the review continues. A passing check or committed
change does not close the broader UI review.
