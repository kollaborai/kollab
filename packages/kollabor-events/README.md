# kollabor-events

`kollabor-events` is Kollabor's event bus and hook foundation.

It provides the shared event models, hook registry, hook executor, event
processor, permission models, ready-message aggregation, and small utility
helpers used by the CLI, plugins, engine, agent runtime, and TUI.

## Current Role

- Register and execute hooks for core and plugin-defined event types.
- Order hook execution by priority and preserve hook status/error metadata.
- Provide event, command, permission, and conversation data models.
- Store cross-package services on the event bus for loose coupling.
- Provide utility helpers for safe execution and nested dict access.

## Architecture

| Module | Responsibility |
|---|---|
| `bus.py` | `EventBus`, service registry, hook registration, event emission |
| `models.py` | event types, hooks, command definitions, UI models |
| `registry.py` | hook storage and enable/disable/status lookup |
| `executor.py` | hook execution, timeout/retry/error behavior |
| `processor.py` | event phase mapping and ordered processing |
| `hook_adapter.py` | sync/async hook compatibility |
| `permissions_models.py` / `permissions_config.py` | approval and risk models |
| `data_models.py` | conversation/session metadata models |
| `ready_message.py` | startup/ready message collection |
| `dict_utils.py` / `error_utils.py` | safe utility helpers |

## Hook Priorities

| Priority | Value | Purpose |
|---|---:|---|
| `SYSTEM` | `1000` | core lifecycle/system hooks |
| `SECURITY` | `900` | permission and policy hooks |
| `PREPROCESSING` | `500` | input/request transforms |
| `LLM` | `100` | LLM orchestration hooks |
| `POSTPROCESSING` | `50` | response/result transforms |
| `DISPLAY` | `10` | display/UI hooks |

## Usage

```python
from kollabor_events import EventBus, EventType, Hook, HookPriority

bus = EventBus()


async def on_tool_pre(data, event):
    data["checked"] = True
    return data


await bus.register_hook(Hook(
    plugin_name="example",
    name="check_tool",
    event_type=EventType.TOOL_CALL_PRE,
    priority=HookPriority.SECURITY.value,
    callback=on_tool_pre,
))

result = await bus.emit_with_hooks(
    EventType.TOOL_CALL_PRE,
    {"tool_data": {"type": "terminal"}},
    source="example",
)
```

## Known Gaps

- The event model is broad and shared across many packages; event ownership and
  schema expectations should be documented closer to each event type.
- String-based custom events are supported, but typed contracts are stronger for
  core events.
- Hook error/timeout behavior is configurable, so callers should test important
  security and lifecycle hooks under failure conditions.

## Roadmap

### Phase 1: Event contract docs

- Document core event payload schemas and expected mutation behavior.
- Add examples for common hook categories: security, display, LLM, and plugins.
- Link event types to the package that owns their primary behavior.

### Phase 2: Safer extension points

- Add optional typed payload helpers for high-risk events.
- Expand tests for cancellation, timeout, retry, and hook ordering behavior.
- Make plugin-defined event naming conventions explicit.

### Phase 3: Observability

- Improve hook status snapshots for diagnostics and UI display.
- Add structured hook execution traces for debugging event-heavy flows.

## Development

Targeted validation examples:

```bash
python -m py_compile packages/kollabor-events/src/kollabor_events/*.py
python -m pytest tests/unit/test_hub_native_param_compat.py -q
```

## Dependencies

None. This package is intentionally stdlib-only.

## License

MIT
