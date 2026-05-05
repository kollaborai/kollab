# kollabor-plugins

`kollabor-plugins` is the plugin framework for Kollabor.

It provides plugin discovery, safe instantiation, lifecycle management, status
collection, the base plugin class, and SDK helpers. Concrete plugins live in the
repo-level `plugins/` directory or installed package plugin locations.

## Current Role

- Discover plugin classes from development and installed plugin directories.
- Instantiate plugins with shared dependencies such as event bus, config, and
  services.
- Provide a stable `BasePlugin` lifecycle for initialization, hook registration,
  status, config widgets, and shutdown.
- Collect plugin startup/status information without crashing the host app.
- Provide utility helpers for safe optional-method calls.

## Architecture

| Module | Responsibility |
|---|---|
| `base.py` | `BasePlugin` lifecycle and default methods |
| `discovery.py` | plugin class discovery |
| `factory.py` | dependency-aware plugin construction |
| `registry.py` | plugin registration and lifecycle registry |
| `collector.py` | status/startup info collection |
| `plugin_sdk.py` | helper API for plugin authors |
| `plugin_utils.py` | safe calls, metadata, interface validation |

## Plugin Shape

```python
from kollabor_plugins import BasePlugin


class MyPlugin(BasePlugin):
    name = "my_plugin"
    version = "1.0.0"
    description = "Adds one useful thing"

    async def initialize(self, args=None, **kwargs):
        return None

    async def register_hooks(self):
        return None

    async def shutdown(self):
        return None
```

Discovery order:

1. `./plugins/`
2. Installed package plugin locations.

Lifecycle:

1. Discover plugin classes.
2. Instantiate with dependencies.
3. Call `initialize()`.
4. Call `register_hooks()`.
5. Call `shutdown()` on app exit.

## Known Gaps

- Plugin metadata and config schemas are partly convention-based; stronger typed
  metadata would help UI and docs generation.
- Discovery order and dependency injection behavior should be covered by more
  integration tests across local and installed plugin layouts.
- Plugin shutdown must remain best-effort and non-blocking; long-running plugin
  tasks need clearer ownership and cancellation guidance.

## Roadmap

### Phase 1: Authoring clarity

- Add a plugin author guide generated from real `BasePlugin` methods.
- Document config widget and status-line conventions.
- Add examples for hook-only, command, fullscreen, and background-task plugins.

### Phase 2: Lifecycle hardening

- Add tests for plugin discovery, instantiation failures, and shutdown failures.
- Track plugin-owned tasks/resources explicitly.
- Make startup/status collection easier to inspect from CLI and engine surfaces.

### Phase 3: Packaging and marketplace readiness

- Normalize plugin metadata fields for installed packages.
- Document compatibility/version expectations for plugins.
- Prepare a stable manifest format if plugins move toward external distribution.

## Development

Targeted validation examples:

```bash
python -m py_compile packages/kollabor-plugins/src/kollabor_plugins/*.py
python -m pytest tests/unit/commands/test_all_command_handlers.py -q
```

## Dependencies

- `kollabor-events`

## License

MIT
