---
title: "Troubleshooting"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Troubleshooting

Common issues and how to debug them.

## Logs Location

Application logs are written daily with rotation:

```
~/.kollab/projects/<encoded-path>/logs/kollab.log
~/.kollab/projects/<encoded-path>/logs/kollab.log.2025-02-23
```

Encoded path: `/home/user/myproject` → `home_user_myproject`

View logs in real-time:
```bash
tail -f ~/.kollab/projects/$(pwd | sed 's/\//_/g')/logs/kollab.log
```

## Debug Mode

Enable debug logging:

```bash
# Via environment variable
KOLLAB_LOG_LEVEL=debug kollab

# Or via config
~/.kollab/config.json:
{
  "kollabor": {
    "log_level": "debug"
  }
}
```

## Common Issues

### Duplicate Input Box After Modal Closes

Symptom: After closing a modal, two input boxes appear.

Cause: Modal didn't use MessageDisplayCoordinator properly.

Fix: Use coordinator pattern for all modals:
```python
self.coordinator.enter_alternate_buffer()
try:
    # ... modal code ...
finally:
    self.coordinator.exit_alternate_buffer(restore_state=True)
```

See docs/architecture/terminal-rendering-architecture.md#modal-pattern

### Messages Not Appearing

Symptom: LLM response chunks don't display.

Cause: Bypassed MessageDisplayCoordinator or writing_messages flag stuck.

Fix: Always use coordinator for messages:
```python
renderer.message_coordinator.display_message_sequence([
    ("assistant", "content")
])
```

Check queue status:
```python
status = renderer.message_coordinator.get_queue_status()
```

### SIGTTIN on macOS

Symptom: Process receives SIGTTIN and hangs.

Cause: Used asyncio.to_thread() with subprocess.run().

Fix: Never use asyncio.to_thread() for subprocesses. Use sync subprocess.run()
directly or asyncio.create_subprocess_exec().

### Terminal Colors Wrong

Symptom: Colors missing or incorrect.

Check color mode:
```bash
echo $COLORTERM
echo $TERM_PROGRAM
```

Manual override:
```bash
KOLLAB_COLOR_MODE=truecolor kollab
KOLLAB_COLOR_MODE=256 kollab
KOLLAB_COLOR_MODE=none kollab
```

### Plugin Not Loading

Symptom: Plugin in plugins/ not discovered.

Check plugin location (development mode):
```bash
ls -la plugins/
```

Only works when running via `python main.py`, not pip install.

For installed version, package must install to site-packages/plugins/.

Check plugin has proper structure:
```python
class MyPlugin(BasePlugin):
    def __init__(self, event_bus, config):
        super().__init__(event_bus, config)
        self.name = "my_plugin"

    async def register_hooks(self):
        ...
```

### MCP Server Connection Fails

Symptom: "Failed to connect to MCP server" error.

Check server config:
```bash
cat ~/.kollab/mcp/mcp_settings.json
```

Verify server command works manually:
```bash
uvx mcp-server-name
```

Check logs for detailed error.

See docs/mcp/MCP_SETUP.md for setup guide.

### Config Not Loading

Symptom: Changes to config.json ignored.

Check config syntax:
```bash
cat ~/.kollab/config.json | python -m json.tool
```

Config is cached. Restart application to reload.

For plugin config, check plugin_config.json in plugin directory.

### LLM Profile Not Found

Symptom: "Profile not found" error.

List available profiles:
```bash
kollab
/profile list
```

Check profile in config:
```bash
cat ~/.kollab/config.json | grep -A 10 profiles
```

### Agent Not Found

Symptom: "Agent not found" error.

List agents:
```bash
ls ~/.kollab/agents/
ls .kollab/agents/
```

Agent must be directory with system_prompt.md:
```
.kollab/agents/my-agent/
    system_prompt.md
    agent.json  # optional
    skill.md    # optional
```

### Slow Startup

Symptom: Application takes >5 seconds to start.

Check what's blocking:
- network calls (version check, API connectivity)
- trender subprocess calls (system prompt rendering)
- plugin initialization

Enable debug logging to see timing:
```bash
KOLLAB_LOG_LEVEL=debug kollab 2>&1 | grep "startup"
```

Disable version check in config:
```json
{
  "kollabor": {
    "check_version": false
  }
}
```

### Import Errors

Symptom: "ModuleNotFoundError" after extraction.

Check PYTHONPATH includes package src:
```bash
echo $PYTHONPATH
```

For development, run from repo root:
```bash
python main.py
```

Or install in development mode:
```bash
pip install -e .
pip install -e ".[dev]"
```

### Circular Import Errors

Symptom: "ImportError: cannot import name" or "circular import".

Cause: Package imports itself indirectly.

Fix: Use TYPE_CHECKING for type hints:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kollabor_config.manager import ConfigManager
```

Move imports inside functions if needed.

## Getting Help

1. Check logs: `~/.kollab/projects/<encoded-path>/logs/kollab.log`
2. Enable debug mode: `KOLLAB_LOG_LEVEL=debug`
3. Search existing issues: github.com/kollaborai/kollab/issues
4. Create issue with:
   - Kollab version: `/version`
   - OS and terminal
   - Full log output
   - Steps to reproduce
