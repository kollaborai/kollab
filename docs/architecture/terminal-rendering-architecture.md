---
title: "Terminal Rendering Architecture"
doc_type: architecture-reference
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Terminal Rendering Architecture

The terminal rendering system (kollabor-tui) is responsible for all visual
output including messages, input, status areas, and modals. It's built as a
layered facade coordinating multiple specialized subsystems.

## Core Components

### TerminalRenderer (Facade)

Main entry point coordinating all rendering.

```python
from kollabor_tui import TerminalRenderer

renderer = TerminalRenderer(event_bus, config)
# Access via: app.terminal_renderer in main application
```

Key responsibilities:
- owns TerminalState (global width/height, color mode)
- owns VisualEffects (color palettes, gradients)
- owns LayoutManager (screen regions)
- owns MessageRenderer (message formatting)
- owns MessageDisplayCoordinator (atomic message display)
- owns ThinkingAnimationManager (thinking indicators)

DO NOT directly manipulate these properties:
- input_line_written, last_line_count, _last_render_content, writing_messages

Instead use MessageDisplayCoordinator methods.

### MessageDisplayCoordinator (State Manager)

CRITICAL for preventing race conditions. Coordinates message display AND
render state.

```python
from kollabor_tui.message_coordinator import MessageDisplayCoordinator

# Display messages atomically
renderer.message_coordinator.display_message_sequence([
    ("system", "Connected to MCP server"),
    ("assistant", "Response text")
])

# Modal transitions
renderer.message_coordinator.enter_alternate_buffer()  # Before modal
# ... render modal ...
renderer.message_coordinator.exit_alternate_buffer(restore_state=True)  # After
```

Why: Direct manipulation causes duplicate input boxes, stale renders. The
coordinator uses flag-based coordination - enter_alternate_buffer() sets
writing_messages=True to block the render loop.

### InputHandler (Input Facade)

Coordinates modular input components.

```python
from kollabor_tui.input_handler import InputHandler

handler = InputHandler(event_bus, renderer, config)
await handler.start()  # Enters raw mode, starts input loop
```

The constructor accepts:
- `event_bus` - EventBus for event emission
- `renderer` - TerminalRenderer for display updates
- `config` - ConfigManager for settings
- `shell_command_service` - Optional shell execution service
- `navigation_manager` - Optional navigation manager

Coordinates:
- InputLoopManager: Platform I/O, paste detection
- KeyPressHandler: Key processing, Enter/Escape handling
- CommandModeHandler: Slash commands, menus
- ModalController: All modal types
- PasteProcessor: Paste detection, placeholders
- DisplayController: Display updates, pause/resume

### EventDrivenRenderLoop (Render Loop)

Drives continuous rendering at target FPS.

```python
from kollabor_tui import EventDrivenRenderLoop

render_loop = EventDrivenRenderLoop(
    renderer=app.terminal_renderer,
    target_fps=30,
)
await render_loop.start()
```

Emits EventType.RENDER_FRAME each frame, allowing hooks to inject content.

### TerminalState (Global State)

Single source of truth for terminal dimensions.

```python
from kollabor_tui.terminal_state import get_terminal_size, get_terminal_width

width, height = get_terminal_size()
width = get_terminal_width()
```

WRONG:
```python
import shutil
size = shutil.get_terminal_size()  # NO - bypasses state
```

### LayoutManager (Screen Regions)

Manages layout areas and thinking animations.

```python
from kollabor_tui.render_layout import LayoutManager

layout = LayoutManager()
areas = layout.calculate_layout(width, height)
# Returns dict of area_name -> ScreenRegion
```

Three status areas (A, B, C) with adaptive sizing based on terminal width.

### DesignSystem (Visual Consistency)

ALWAYS use existing design components. Never hardcode colors or boxes.

```python
from kollabor_tui.design_system import T, S, C, Box, TagBox, solid, solid_fg

# Theme access
primary = T().primary
text = T().text
success = T().success

# Solid block rendering
solid_fg("▄" * width, T().dark[0])  # Top edge
solid(f"   {text:<{w}}", T().dark[0], T().text, w)  # Content

# Tag box pattern
TagBox.render(
    lines=["content"],
    tag_bg=T().primary[0],
    tag_fg=T().text_dark,
    tag_width=3,
    content_colors=T().dark[0],
    content_fg=T().text,
)
```

## Render Flow

```
EventDrivenRenderLoop (30 FPS target)
    ↓
emit(RENDER_FRAME)
    ↓
hook execution (display priority)
    ↓
TerminalRenderer.render_frame()
    ↓
LayoutManager.calculate_layout()
    ↓
render status areas (A, B, C)
    ↓
render input line
    ↓
flush to stdout
```

## Message Display Flow

```
LLMService emits response chunks
    ↓
StreamingHandler accumulates
    ↓
MessageDisplayService formats
    ↓
MessageDisplayCoordinator.display_message_sequence()
    ↓
set writing_messages=True (blocks render loop)
    ↓
render all messages atomically
    ↓
set writing_messages=False
    ↓
trigger render frame
```

## Modal Pattern

All fullscreen modals MUST use coordinator pattern:

```python
async def _show_my_modal(self) -> bool:
    self.coordinator.enter_alternate_buffer()
    try:
        sys.stdout.write('\033[?1049h')  # Enter alternate buffer
        sys.stdout.write('\033[?25l')    # Hide cursor
        sys.stdout.flush()
        # ... render and handle input ...
    finally:
        sys.stdout.write('\033[?25h')    # Show cursor
        sys.stdout.write('\033[?1049l')  # Exit alternate buffer
        sys.stdout.flush()
        self.coordinator.exit_alternate_buffer(restore_state=True)
    return result
```

## Widgets

Reusable UI components in kollabor_tui.widgets/:

- CheckboxWidget
- DropdownWidget
- SliderWidget
- TextInputWidget
- LabelWidget
- ProgressWidget
- SpinBoxWidget

Always use existing widgets. Don't create new UI patterns.

## Color Support

Auto-detected modes: TRUE_COLOR (24-bit), EXTENDED (256-color), BASIC (16-color), NONE

Detection order: COLORTERM env var → TERM_PROGRAM → TERM variable → Apple Terminal (256-color)

Manual override:
```bash
KOLLAB_COLOR_MODE=256 kollab
KOLLAB_COLOR_MODE=truecolor kollab
KOLLAB_COLOR_MODE=none kollab
```

## Key Imports

```python
# Core rendering
from kollabor_tui.terminal_renderer import TerminalRenderer
from kollabor_tui.message_coordinator import MessageDisplayCoordinator
from kollabor_tui.input_handler import InputHandler
from kollabor_tui import EventDrivenRenderLoop

# State
from kollabor_tui.terminal_state import (
    get_terminal_size,
    get_terminal_width,
    get_terminal_height,
    get_global_terminal_state,
)

# Design system
from kollabor_tui.design_system import T, S, C, Box, TagBox, solid, solid_fg

# Message handling
from kollabor_tui.message_renderer import MessageRenderer, MessageType
from kollabor_tui.message_display_service import MessageDisplayService

# Layout
from kollabor_tui.render_layout import LayoutManager, ScreenRegion
```
