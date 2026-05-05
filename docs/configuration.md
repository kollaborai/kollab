---
title: "Configuration"
created: 2026-02-24
modified: 2026-04-09
status: active
---
# Configuration

Kollab's configuration system is layered: global defaults, project settings, and local overrides can all coexist.

## Profiles System

Profiles define how you connect to LLM providers. Each profile specifies the provider, model, API key, and parameters.

### Profile Fields

| Field | Type | Description |
|-------|------|-------------|
| `provider` | string | `openai`, `anthropic`, `azure_openai`, `gemini`, `openai_responses`, `openrouter`, `custom`, `auto` |
| `model` | string | Model identifier (e.g., `gpt-5.4`, `claude-sonnet-4-6`) |
| `api_key` | string | API authentication key |
| `base_url` | string | Custom endpoint URL (for custom providers) |
| `temperature` | float | Sampling randomness (0.0-2.0, default: 0.7) |
| `max_tokens` | int | Maximum tokens to generate (default: 4096) |
| `timeout` | float | Request timeout in seconds (default: 60.0) |

### Environment Variable Pattern

Create profiles on-the-fly using `KOLLAB_{NAME}_{FIELD}`:

```bash
# Syntax: KOLLAB_<PROFILE_NAME>_<FIELD>=value
KOLLAB_WORK_PROVIDER=anthropic
KOLLAB_WORK_API_KEY="<your-anthropic-api-key>"
KOLLAB_WORK_MODEL=claude-sonnet-4-6
KOLLAB_WORK_TEMPERATURE=0.5

kollab --profile work
```

Profile names are case-insensitive but typically use uppercase for consistency.

### Managing Profiles Interactively

The `/profile` command provides interactive profile management:

```
/profile list                    # List all profiles
/profile show <name>             # Show profile details
/profile set <name>              # Switch active profile
/profile create                  # Create new profile interactively
/profile delete <name>           # Delete a profile
```

Profiles are stored in `config.json` under the `kollabor.llm.profiles` key.

## Config File Locations

Kollab uses a layered configuration system. Settings are loaded and merged in this order:

1. **Global config** - Base layer, applies to all projects
2. **Project config** - Project-specific defaults
3. **Local config** - Local override (optional)

### Global Config

```
~/.kollab/config.json
```

Default location for user-wide settings. Created automatically on first run.

### Project Config

```
~/.kollab/projects/<encoded-path>/config.json
```

Each project directory gets its own config. The path is encoded:
- `/home/user/myproject` → `home_user_myproject`

### Local Config (Optional)

```
.kollab/config.json
```

If present in your project root, this overrides global and project settings.

Use this for per-project customization that shouldn't be shared (e.g., API keys for local development).

### Config Priority

When settings conflict, the priority is:
```
local > project > global > defaults
```

Changes via `/profile` and other commands save to the highest-priority existing config.

## Configuration Structure

The base config is divided into sections:

```json
{
  "kollabor": {
    "llm": {
      "auto_detect_provider": true,
      "max_history": 90,
      "save_conversations": true,
      "conversation_format": "jsonl",
      "oauth": {
        "auto_refresh": true,
        "token_expiry_buffer_seconds": 300
      }
    }
  },
  "terminal": {
    "render_fps": 20,
    "status_lines": 4,
    "thinking_effect": "shimmer",
    "interactive_shell": true
  },
  "input": {
    "ctrl_c_exit": true,
    "history_limit": 100,
    "paste_detection_enabled": true
  },
  "logging": {
    "level": "INFO",
    "format_type": "compact"
  },
  "plugins": {
    "modern_input": {
      "enabled": true,
      "width_mode": "auto"
    }
  }
}
```

### Accessing Config Values

The config system uses dot notation for nested access:

```python
config.get("kollabor.llm.max_history", 90)
config.get("terminal.render_fps", 20)
```

## Project Data Directory

Each project has its own data directory:

```
~/.kollab/projects/<encoded-path>/
├── config.json           # Project-specific config
├── conversations/        # Conversation history (JSONL)
│   ├── raw/              # Raw API logs
│   ├── memory/           # Intelligence cache
│   └── snapshots/        # Conversation snapshots
└── logs/                 # Application logs (daily rotation)
```

The encoded path transforms separators to underscores:
- `/Users/john/dev/myapp` → `Users_john_dev_myapp`

## Color Modes

Kollab auto-detects terminal color support, but you can override:

```bash
# Force specific color mode
KOLLAB_COLOR_MODE=truecolor kollab   # 24-bit color
KOLLAB_COLOR_MODE=256 kollab          # 256-color
KOLLAB_COLOR_MODE=basic kollab        # 16-color
KOLLAB_COLOR_MODE=none kollab         # No colors
```

### Detection Order

Without override, detection follows:
1. `COLORTERM` environment variable
2. `TERM_PROGRAM` (iTerm, Terminal.app, etc.)
3. `TERM` variable (`xterm-256color`, etc.)
4. Apple Terminal (defaults to 256-color)
5. Falls back to 16-color basic

### Color Modes

| Mode | Colors | When Used |
|------|--------|-----------|
| `truecolor` | 16M (24-bit) | Modern terminals (iTerm2, WezTerm, kitty) |
| `256` | 256 | Terminal.app, older iTerm, tmux |
| `basic` | 16 | Fallback for unknown terminals |
| `none` | 1 | Explicitly disabled or pipes |

## System Prompts

System prompts define the AI's behavior. They support dynamic content via `<trender>` tags.

### Priority Order

1. `KOLLAB_SYSTEM_PROMPT` environment variable (direct content)
2. `KOLLAB_SYSTEM_PROMPT_FILE` environment variable (file path)
3. `.kollab/agents/default/system_prompt.md` (local project)
4. `~/.kollab/agents/default/system_prompt.md` (global)
5. Built-in fallback

### Dynamic Tags

```markdown
<!-- Project structure tree -->
<trender type="project_tree" max_depth="3" />

<!-- List files matching pattern -->
<trender type="file_list" pattern="**/*.py" exclude="__pycache__" />

<!-- Include file contents -->
<trender type="file_content" path="README.md" />

<!-- Current timestamp -->
<trender type="timestamp" format="%Y-%m-%d %H:%M:%S" />
```

Commands are executed at startup with a 5-second timeout each.

### File Paths

When using `<trender type="file_content" path="sections/intro.md" />`, the path is resolved relative to the system prompt file's directory. This allows modular system prompts with includes.

## Resetting Configuration

To reset all configs to defaults:

```bash
kollab --reset-config
```

This recreates:
- `~/.kollab/config.json`
- `.kollab/config.json` (if exists)

Back up your `config.json` before resetting if you have custom profiles.

## Plugin Configuration

Plugins can define their own configuration sections, which merge into the main config under the `plugins` key:

```json
{
  "plugins": {
    "my_plugin": {
      "enabled": true,
      "custom_setting": "value"
    }
  }
}
```

Plugin config schemas are auto-discovered and validated. Use `/profile` or edit `config.json` directly to modify plugin settings.
