---
title: "AltView Framework"
doc_type: architecture-reference
created: 2026-04-02
modified: 2026-04-02
status: active
---
# AltView Framework

Unified alternate-buffer screen system for Kollab. Replaces the
single-shot FullScreenPlugin model with persistent, stackable, resumable
views that support background tasks and display queue replay.


## Overview

AltView lets plugins take full control of the terminal alternate buffer.
Unlike the old FullScreenPlugin (create -> run -> destroy), AltView
sessions persist across enter/exit cycles. A user can `/example mytask`,
exit back to chat, and re-enter the same session later with all state
intact -- including the results of background tasks that kept running.

Core capabilities:
- Lifecycle-managed views with five states
- Named sessions that survive suspend/resume cycles
- Stackable views (open an AltView from inside another AltView)
- Background task tracking with automatic SUSPENDED -> IDLE transition
- DisplayQueue that captures frames while suspended and replays at 3x
- Status widget showing session names and idle indicators
- Auto-discovery and slash command registration from `plugins/altview/`


## Architecture

```
kollabor/altview/command_integration.py     (discovery + slash commands)
       |
       v
packages/kollabor-tui/src/kollabor_tui/altview/
       |-- base.py              AltView base class + AltViewMetadata + AltViewState
       |-- session.py           AltViewSession (terminal setup, input hooks, render loop)
       |-- stack_manager.py     AltViewStackManager (push/pop, registry, replay)
       |-- display_queue.py     DisplayQueue (frame capture + accelerated replay)
       |
plugins/altview/
       |-- example_altview.py   Reference implementation
       |-- matrix_altview.py    Matrix rain effect
       |-- conversations_altview.py
       |-- login_altview.py
       |-- mcp_wizard_altview.py
```

Data flow:

```
User types /example mytask
    -> AltViewCommandIntegrator parses session name "mytask"
    -> Gets or creates ExampleAltView instance (cached by session name)
    -> AltViewStackManager.push(altview, "mytask")
        -> AltViewSession.enter()
            -> Registers FULLSCREEN_INPUT hook on event bus
            -> Sets up terminal alternate buffer via FullScreenRenderer
            -> Calls altview.on_enter(renderer) or altview.on_resume()
            -> Sets state to RUNNING
        -> AltViewSession.run_loop()
            -> EventDrivenRenderLoop drives render_frame() + handle_input()
            -> Blocks until user exits
        -> AltViewStackManager._pop_current()
            -> AltViewSession.exit()
                -> Calls altview.on_suspend(), state -> SUSPENDED
                -> Restores terminal, unregisters input hook
                -> Stops DisplayQueue capture
            -> Replays any buffered DisplayQueue frames
```


## Lifecycle

An AltView instance moves through five states:

```
CREATED --> RUNNING --> SUSPENDED --> IDLE --> COMPLETE
              ^            |
              |            v
              +-- RUNNING (on_resume)
```

State transitions:

| From      | To        | Trigger                                        |
|-----------|-----------|------------------------------------------------|
| CREATED   | RUNNING   | First push onto the stack (on_enter called)    |
| RUNNING   | SUSPENDED | User exits view (on_suspend called)            |
| SUSPENDED | RUNNING   | User re-enters same session (on_resume called) |
| SUSPENDED | IDLE      | All background tasks complete                  |
| IDLE      | RUNNING   | User re-enters (on_resume called)              |
| *any*     | COMPLETE  | Session destroyed (on_complete called)         |

Methods called at each transition:

- `create_session(name)` -- framework assigns session identity
- `on_enter(renderer)` -- first time only; store renderer, init UI
- `on_resume()` -- subsequent entries; refresh cached state
- `render_frame(delta_time)` -- called per frame while RUNNING
- `handle_input(key_press)` -- called on keypress while RUNNING
- `on_suspend()` -- moving to background; state persists
- `on_complete()` -- teardown; framework cancels remaining bg tasks


## DisplayQueue

When a view is suspended, its DisplayQueue can continue capturing frames
(if another view runs above it on the stack). On resume, captured frames
replay at accelerated speed so the user sees what happened while away.

Constants:
- `REPLAY_SPEED = 3.0` -- frames play at 3x their original timing
- `MAX_BUFFER_SECONDS = 30.0` -- if buffered duration exceeds this,
  trim to last 30 frames before replaying
- `MAX_FRAMES = 900` -- hard cap on stored frames (~30s at 30fps)

Each frame stores:
- `timestamp` -- monotonic time of capture
- `render_content` -- full terminal output string
- `frame_number` -- sequential counter

Replay preserves relative timing between frames, compressed by the
replay speed factor. During replay, the user can press any key to
trigger `request_skip()`, which jumps straight to the final frame.

```python
# Capture lifecycle (managed by AltViewSession)
display_queue.start_capture()
display_queue.capture_frame(rendered_content)
display_queue.stop_capture()

# Replay (managed by AltViewStackManager on pop)
await display_queue.replay(render_fn)
display_queue.clear()
```


## Stacking

AltViews can open other AltViews. The AltViewStackManager maintains a
LIFO stack of sessions. When a new view is pushed, the current one is
suspended. When the top view exits, the previous one resumes.

```
Stack depth 3:
  [2] MCP Wizard  (RUNNING -- foreground)
  [1] Research    (SUSPENDED)
  [0] Editor      (SUSPENDED)
```

- Maximum stack depth: 6 (`MAX_STACK_DEPTH`)
- Each stack level has its own AltViewSession with its own DisplayQueue
- Push blocks until the user exits that view (push is synchronous from
  the caller's perspective via `await stack_mgr.push()`)
- On pop, the exited session's DisplayQueue replays into the view
  below it (or stdout if returning to mainview)

The stack manager emits `MODAL_SHOW` on push and `MODAL_HIDE` on pop
for compatibility with the rest of the UI framework (input gating,
render loop pausing, etc).


## Named Sessions

Sessions persist in the stack manager's registry by name. This enables
re-entry without recreating the plugin instance.

```
/example mytask     --> creates session "mytask" with ExampleAltView
(user exits)        --> session suspended, stays in registry
/example mytask     --> resumes "mytask" (on_resume, not on_enter)
/example other      --> creates new session "other"
```

If no session name is provided, one is auto-generated:
`{plugin_type}-{sha256_prefix}` (e.g. `example-a1b2c3d4`).

Registry operations:
- `get_session(name)` -- look up by name
- `get_all_sessions()` -- full registry copy
- `get_session_infos()` -- lightweight SessionInfo list for status widgets
- `destroy_session(name)` -- permanent teardown (fails if on stack)
- `destroy_all_sessions()` -- destroy everything not on stack

The command integrator also caches plugin instances by session name in
`_plugin_instances`, so the same AltView object is reused when the user
re-enters a named session.


## Background Tasks

AltView plugins can spawn tracked async tasks that continue running
even when the view is suspended.

```python
async def handle_input(self, key_press):
    if key_press.char == "b":
        self.spawn_background_task(
            self._do_work(),
            name="worker",
        )
    return False

async def _do_work(self):
    # This runs even when the view is suspended
    for i in range(10):
        await asyncio.sleep(1.0)
        self.progress = i + 1
```

Task tracking:
- Tasks are stored in `self._background_tasks`
- Each task gets a done callback via `_on_task_done`
- When a task finishes, it is removed from the list
- If the view is SUSPENDED and no tasks remain, state -> IDLE
- On `on_complete()`, all remaining tasks are cancelled
- Task names follow the pattern `altview:{session}:{name}`

The SUSPENDED -> IDLE transition is automatic. The status widget uses
this to show `(idle)` next to sessions with completed background work.

To enable background tasks, set `supports_background=True` in metadata.


## Status Widget

File: `packages/kollabor-tui/src/kollabor_tui/status/altview_widget.py`

Renders in the status bar when sessions exist. Format:

```
altview: research  data-fetch(idle)
```

- Active/running sessions: primary color
- Suspended sessions: dim text
- Idle/completed sessions: warning color + `(idle)` suffix

The widget finds the stack manager via event bus service lookup:
`event_bus.get_service("altview_stack_manager")`. When no sessions
exist, it returns empty string (hides itself).

If the session list is too wide for the terminal, it collapses to:
`altview: 3 views (1 idle)`


## Creating an AltView Plugin

### Minimal plugin

Create `plugins/altview/my_plugin_altview.py`:

```python
from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress


class MyAltView(AltView):
    def __init__(self):
        super().__init__(AltViewMetadata(
            plugin_type="myplugin",       # becomes /myplugin
            description="My custom view",
            aliases=["mp"],               # also accessible as /mp
            supports_named_sessions=True,
            supports_background=False,
        ))
        self.target_fps = 15.0

    async def on_enter(self, renderer):
        self._renderer = renderer

    async def render_frame(self, delta_time):
        if not self.renderer:
            return False

        width, height = self.renderer.get_terminal_size()
        self.renderer.clear_screen()
        self.renderer.write_at(
            width // 2 - 5, height // 2,
            "My AltView",
        )
        return True

    async def handle_input(self, key_press):
        if key_press.name == "Escape" or key_press.char == "q":
            return True   # exit
        return False      # keep running
```

That's it. Drop the file in `plugins/altview/` and the framework
auto-discovers it on startup. The `plugin_type` becomes the slash
command name; aliases create additional command names.

### Adding background tasks

```python
class WorkerAltView(AltView):
    def __init__(self):
        super().__init__(AltViewMetadata(
            plugin_type="worker",
            description="Background worker demo",
            supports_background=True,
        ))
        self.result = None

    async def on_enter(self, renderer):
        self._renderer = renderer
        # Start work immediately on entry
        self.spawn_background_task(
            self._fetch_data(),
            name="fetch",
        )

    async def _fetch_data(self):
        await asyncio.sleep(5.0)
        self.result = "data loaded"

    async def render_frame(self, delta_time):
        self.renderer.clear_screen()
        status = self.result or "loading..."
        self.renderer.write_at(2, 2, f"Status: {status}")
        return True

    async def handle_input(self, key_press):
        return key_press.char == "q"
```

The user can exit while data loads. The status widget shows the session
name. When the task finishes, `(idle)` appears. Re-entering shows the
completed result.

### Using the design system

```python
from kollabor_tui.design_system import T, C, solid, solid_fg

theme = T()
width, height = self.renderer.get_terminal_size()

# Top edge
self.renderer.write_at(x, y, solid_fg(C["half_bottom"] * w, theme.primary[0]), "")

# Content row
self.renderer.write_at(x, y+1, solid("  content  ", theme.dark[0], theme.text, w), "")

# Bottom edge
self.renderer.write_at(x, y+2, solid_fg(C["half_top"] * w, theme.dark[0]), "")
```

### Accessing the app

If your plugin needs the app reference (config, profile manager, etc.),
implement `set_app()`:

```python
class MyAltView(AltView):
    def set_app(self, app):
        self.app = app

    def set_managers(self, config, profile_manager):
        self.config = config
        self.profile_manager = profile_manager
```

The command integrator calls these before pushing the view if they exist.


## Plugin Discovery

Discovery flow:
1. `application.py` calls `_initialize_altview_commands()` during startup
2. `AltViewCommandIntegrator` is created with command registry, event bus,
   terminal renderer, config, profile manager, and app reference
3. `discover_and_register_plugins(plugins_dir)` scans `plugins/altview/`
4. Each `.py` file is loaded with `importlib.util`
5. The first class that inherits from `AltView` (not the base itself) is used
6. A temp instance is created to read its `AltViewMetadata`
7. A `CommandDefinition` is registered with `CommandMode.ALTVIEW`
8. The handler creates/reuses the plugin instance and pushes onto the stack

File naming convention: `{name}_altview.py` (not enforced, just convention).

The `__init__.py` in `plugins/altview/` can be empty. Only files not
starting with `__` are scanned.


## Migration from FullScreenPlugin

Key differences between FullScreenPlugin and AltView:

| FullScreenPlugin          | AltView                           |
|---------------------------|-----------------------------------|
| Single-shot (start/stop)  | Persistent (suspend/resume)       |
| No state after exit       | State survives across cycles      |
| No stacking               | Stack up to 6 deep               |
| No background tasks       | Tracked background tasks          |
| No display queue           | Frame capture + 3x replay         |
| `PluginMetadata`          | `AltViewMetadata`                 |
| `plugins/fullscreen/`     | `plugins/altview/`                |
| `CommandMode.MODAL`       | `CommandMode.ALTVIEW`             |

Method mapping:

| FullScreenPlugin       | AltView                                   |
|------------------------|-------------------------------------------|
| `__init__(metadata)`   | `__init__(metadata)` -- same pattern      |
| `initialize(renderer)` | `on_enter(renderer)` -- called each entry |
| `on_start()`           | `on_enter(renderer)` -- merged into entry |
| `render_frame(dt)`     | `render_frame(dt)` -- same signature      |
| `handle_input(key)`    | `handle_input(key)` -- same signature     |
| `on_stop()`            | `on_suspend()` -- session persists        |
| `cleanup()`            | `on_complete()` -- permanent teardown     |
| (none)                 | `on_resume()` -- re-entry after suspend   |
| (none)                 | `spawn_background_task(coro, name)`       |

Migration steps:
1. Move file from `plugins/fullscreen/` to `plugins/altview/`
2. Change base class from `FullScreenPlugin` to `AltView`
3. Change `PluginMetadata` to `AltViewMetadata`
4. Replace `initialize(renderer)` with `on_enter(renderer)`
5. Replace `on_stop()` with `on_suspend()` (for exit) and `on_complete()`
   (for teardown)
6. Add `on_resume()` if state needs refreshing on re-entry
7. Store renderer in `self._renderer` (the base class property reads it)
8. Add `supports_named_sessions` / `supports_background` to metadata
9. Remove `update_frame_stats()` calls (framework handles it)
