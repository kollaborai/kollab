# kollabor-tui

`kollabor-tui` is the terminal UI package for Kollabor.

It owns rendering primitives, terminal state, input parsing, design-system
components, widgets, status layout, modals, fullscreen sessions, message display,
tool display, and alternate-view support. TUI code in the app should use these
components rather than writing directly to terminal renderer internals.

## Current Role

- Provide the design system used by messages, status widgets, modals, and tools.
- Track terminal size and layout state through shared helpers.
- Coordinate message display and alternate-buffer/fullscreen transitions.
- Parse keyboard input and route it through modal/status/input controllers.
- Render tool calls, thinking blocks, profile modals, command menus, and widgets.
- Provide reusable widgets for plugin/config UI surfaces.

## Architecture

| Module/Directory | Responsibility |
|---|---|
| `design_system/` | themes, colors, icons, gradients, boxes, inline widgets |
| `terminal_state.py` | terminal dimensions and global state helpers |
| `terminal_renderer.py` | main terminal active-area renderer |
| `message_coordinator.py` | message flow and alternate-buffer coordination |
| `message_display_service.py` | message display policy and suppression logic |
| `message_renderer.py` / `tool_display.py` | message/tool formatting |
| `render_loop.py` / `render_layout.py` | render triggers and screen regions |
| `input/` / `input_handler.py` | input loop, key handling, command mode, paste |
| `status/` | status layout, widgets, navigation, inline editors |
| `modals/` | modal state, overlays, actions, renderers |
| `fullscreen/` | fullscreen plugin/session manager and renderer |
| `altview/` | alternate-view stack/session/display queue |
| `widgets/` | text, dropdown, checkbox, tree, slider, file browser widgets |
| `display_tap.py` / `buffer_manager.py` | display capture and buffering |

## Usage

```python
from kollabor_tui import T, get_terminal_size, solid_fg

width, height = get_terminal_size()
line = solid_fg("hello".ljust(width), T().primary)
```

For application rendering, prefer `MessageDisplayCoordinator`, fullscreen
manager/session APIs, and terminal-state helpers. Avoid direct manipulation of
`TerminalRenderer` render-state internals.

## Known Gaps

- Rendering behavior is sensitive to terminal lifecycle, alternate buffers, and
  resize events; risky changes need read-only lifecycle tracing before patches.
- Some display policy lives across `message_display_service.py`,
  `message_coordinator.py`, and `tool_display.py`; regression tests should cover
  classification/display paths together.
- The package exposes many public imports from `__init__.py`; a smaller stable
  facade would make downstream usage clearer.
- Visual behavior often needs real terminal or screenshot-style verification in
  addition to unit tests.

## Roadmap

### Phase 1: Rendering stability

- Document the steady-state draw, modal/fullscreen transition, and resize
  recovery contracts.
- Add focused regression tests for tool display classification and suppression.
- Keep terminal size access centralized through `terminal_state.py`.

### Phase 2: Public API cleanup

- Split stable public APIs from compatibility exports.
- Add examples for widgets, status widgets, fullscreen plugins, and altviews.
- Make design-system usage patterns consistent across all TUI surfaces.

### Phase 3: Verification tooling

- Add repeatable terminal/screenshot verification for fullscreen and complex
  render paths.
- Improve diagnostics for render loop hibernation, buffering, and active-area
  state.
- Document plugin UI extension points with real examples.

## Development

Targeted validation examples:

```bash
python -m py_compile packages/kollabor-tui/src/kollabor_tui/*.py
python -m pytest tests/unit/mcp/test_mcp_status_plugin.py -q
```

## Dependencies

- `kollabor-events`

## License

MIT
