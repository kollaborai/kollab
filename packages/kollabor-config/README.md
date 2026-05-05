# kollabor-config

`kollabor-config` is the configuration and path-management package for
Kollabor.

It centralizes config loading, global/project path helpers, plugin config
schemas, migrations, task settings, and hot reload. Other packages should use
this package instead of hardcoding `~/.kollab` paths or parsing config
files directly.

## Current Role

- Load and merge Kollabor configuration from files and environment-compatible
  sources.
- Manage global and project-scoped data directories.
- Provide plugin config schemas and widget metadata.
- Support config migrations and first-run initialization.
- Provide hot reload callbacks when watchdog is available.
- Expose task/queue/background-task config models.

## Architecture

| Module | Responsibility |
|---|---|
| `service.py` | singleton-style config access, save/reload, file watching |
| `loader.py` | lower-level config loading and merge behavior |
| `manager.py` | global/project config manager compatibility layer |
| `config_utils.py` | config directory, logs, conversations, prompt paths |
| `plugin_schema.py` | plugin field/schema/widget definitions |
| `plugin_config_manager.py` | plugin config retrieval and persistence |
| `llm_task_config.py` | LLM task, queue, and background-task settings |
| `migration.py` | config version migration helpers |

## Usage

```python
from kollabor_config import ConfigService, get_plugin_config_manager

config = ConfigService.get_instance()
model = config.get("kollabor.llm.model", "claude-3-5-sonnet")

plugin_config = get_plugin_config_manager().get_plugin_config("my_plugin")
```

## Known Gaps

- Some application code still reaches directly into home-directory paths; those
  paths should move toward these helpers over time.
- Config keys are not yet fully described by one typed registry, so stale keys
  can appear in docs, defaults, or plugin code.
- Hot reload depends on optional watchdog support and callback consumers; code
  that needs reloads should handle the no-watchdog path.
- Root package and workspace package versions can drift during development; the
  release process should keep them synchronized when publishing packages.

## Roadmap

### Phase 1: Key registry

- Create a canonical config key catalog with defaults, types, descriptions, and
  ownership.
- Generate config UI schemas and docs from the same catalog where possible.
- Add tests that catch missing, renamed, or stale config keys.

### Phase 2: Path consolidation

- Replace direct `Path.home() / ".kollab"` usage in callers with package
  helpers.
- Clarify project-scoped versus global data locations.
- Add path migration tests for existing user config/data layouts.

### Phase 3: Reload and migration hardening

- Make reload callback behavior consistent with and without watchdog.
- Add migration dry-run/reporting utilities.
- Improve validation and recovery around corrupted config files.

## Development

Targeted validation examples:

```bash
python -m py_compile packages/kollabor-config/src/kollabor_config/*.py
python -m pytest tests/unit/commands/test_all_command_handlers.py -q
```

## Dependencies

- `kollabor-events`

## License

MIT
