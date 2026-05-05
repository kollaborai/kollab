---
title: "FAQ"
created: 2026-02-24
modified: 2026-04-08
status: active
---
# FAQ

Frequently asked questions about Kollab.

## General

### What is Kollab?

A terminal AI chat application where everything has hooks. Every action triggers customizable hooks that plugins can intercept and modify. Think of it as a pipeline you can tap into at any stage.

### What providers are supported?

Anthropic (Claude), OpenAI (API key or OAuth), Google Gemini, Azure OpenAI, OpenRouter (300+ models), and any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM, etc.).

See [providers.md](providers.md) for the complete list.

### Is it free?

Yes, MIT-licensed. You pay only for the LLM API usage to your provider.

### What Python version?

3.12 or later.

## Setup

### How do I install?

macOS:
  brew install kollaborai/tap/kollab

Cross-platform:
  curl -sS https://raw.githubusercontent.com/kollaborai/kollab/main/install.sh | bash

Python packages:
  uv tool install kollab
  pipx install kollab

See [getting-started.md](getting-started.md) for details.

### How do I use my ChatGPT subscription?

kollab --login openai

This opens a browser for OAuth. Tokens last ~8 days and auto-refresh. Prerequisite: enable "device code authorization" in ChatGPT settings.

### How do I connect to a local LLM?

Use env vars to create a profile:

KOLLAB_OLLAMA_PROVIDER=custom
KOLLAB_OLLAMA_BASE_URL=http://localhost:11434/v1
KOLLAB_OLLAMA_MODEL=llama3.3
kollab --profile ollama

Common local endpoints:
  Ollama:     http://localhost:11434/v1
  LM Studio:  http://localhost:1234/v1
  vLLM:       http://localhost:8000/v1

### Where are my conversations stored?

~/.kollab/projects/<encoded-path>/conversations/

Encoded path: `/home/user/myproject` becomes `home_user_myproject`. Global config is at `~/.kollab/config.json`. Local project config is at `.kollab/config.json`.

## Usage

### How do I switch models?

Use the profile command interactively:
  /profile

Or specify via flag:
  kollab --profile openai

List available profiles:
  /profile list

Create new profile:
  /profile create

See [features/profiles.md](features/profiles.md) for profile management.

### How do I use pipe mode?

Direct query:
  kollab "What is the capital of France?"

From stdin:
  echo "Explain this" | kollab -p

With timeout:
  git diff | kollab "write commit message" -p --timeout 30s

Timeout formats: 30s, 5min, 1h.

In pipe mode, stdin is context, query is instruction.

### What slash commands are available?

Type / to see the menu. Common commands:

  /profile     Manage LLM profiles
  /save        Export conversation (markdown, jsonl, clipboard)
  /permissions Configure tool approvals
  /login       OAuth login
  /mcp         Manage MCP servers
  /resume      Resume previous conversation
  /tmux        Manage tmux sessions
  /help        Show all commands

See [features/slash-commands.md](features/slash-commands.md) for the full list.

### How do I resume a conversation?

Resume most recent:
  /resume

Resume specific conversation:
  /resume <conversation-id>

List conversations in your project directory to find IDs.

### Can I use it in scripts?

Yes, pipe mode is designed for scripting:

  # Get commit message
  git diff | kollab "write commit message" -p

  # Summarize document
  cat doc.txt | kollab "summarize" -p

  # Code review
  cat file.py | kollab "find bugs" -p --timeout 1h

Exit code is 0 on success, 1 on failure.

## Customization

### How do I write a plugin?

Create a Python file in plugins/:

from kollabor_plugins import BasePlugin
from kollabor_events import EventType, Hook, HookPriority

class MyPlugin(BasePlugin):
    async def register_hooks(self):
        await self.event_bus.register_hook(
            Hook(
                name="modify_request",
                plugin_name="myplugin",
                event_type=EventType.LLM_REQUEST_PRE,
                callback=self.on_request,
                priority=HookPriority.LLM.value
            )
        )

    async def on_request(self, data, event):
        # modify request data
        return data

Drop in plugins/ and it auto-loads.

### How do I add custom tools?

Tools are added via plugins. Register a hook for tool-related events and inject tool definitions into the API request context.

See the plugin development guide for examples.

### How do I customize the system prompt?

File-based:
  kollab --system-prompt my-prompt.md

Environment variable:
  export KOLLAB_SYSTEM_PROMPT="You are a helpful assistant..."

Environment file:
  export KOLLAB_SYSTEM_PROMPT_FILE=/path/to/prompt.md

Files support dynamic content via <trender> tags:

  <trender type="project_tree" max_depth="3" />
  <trender type="file_list" pattern="**/*.py" />
  <trender type="file_content" path="README.md" />

See [features/dynamic-system-prompts.md](features/dynamic-system-prompts.md).

### Can I use multiple providers?

Yes. Create profiles for each:

  kollab --profile claude    # Anthropic
  kollab --profile openai   # OpenAI
  kollab --profile local    # Ollama

Profiles are stored in config.json. Switch anytime via /profile.

## Troubleshooting

### Why is my API key not working?

Check the env var is exported:
  echo $ANTHROPIC_API_KEY

For profiles, verify field names:
  KOLLAB_MYPROFILE_API_KEY=...    # correct
  KOLLAB_MYPROFILE_KEY=...        # wrong (missing API_KEY)

Enable debug logging:
  KOLLAB_LOG_LEVEL=debug kollab

See [troubleshooting.md](troubleshooting.md) for detailed debugging.

### Why are colors wrong?

Auto-detection can fail. Manual override:

  KOLLAB_COLOR_MODE=truecolor kollab
  KOLLAB_COLOR_MODE=256 kollab
  KOLLAB_COLOR_MODE=none kollab

Check terminal capabilities:
  echo $COLORTERM
  echo $TERM_PROGRAM

### How do I report a bug?

Open an issue at:
  https://github.com/kollaborai/kollab/issues

Include:
  - Kollab version (run /version)
  - OS and terminal
  - Log output (~/.kollab/projects/<path>/logs/kollab.log)
  - Steps to reproduce

See [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution guidelines.
