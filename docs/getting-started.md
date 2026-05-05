---
title: "Getting Started"
created: 2026-02-24
modified: 2026-04-09
status: active
---
# Getting Started

## Installation

Kollab can be installed via several methods. Choose the one that fits your workflow.

### Homebrew (macOS)

```bash
brew install kollaborai/tap/kollab
```

This installs the `kollab` command system-wide. Use `brew upgrade kollaborai/tap/kollab` to update.

### Curl Installer (Cross-platform)

```bash
curl -sS https://raw.githubusercontent.com/kollaborai/kollab/main/install.sh | bash
```

This installs to `~/.local/bin` by default. Add that directory to your PATH if needed.

### Python Package Managers

```bash
# uv (recommended for speed)
uv tool install kollab

# pipx (isolated environments)
pipx install kollab

# pip (system-wide, may conflict)
pip install kollab
```

For development mode from source:
```bash
git clone https://github.com/kollaborai/kollab.git
cd kollab
pip install -e ".[dev]"
python main.py
```

## Quick Start

Kollab automatically detects API keys from standard environment variables. Just set a key and run:

```bash
export ANTHROPIC_API_KEY="<your-anthropic-api-key>"
kollab
```

That's it. No config files needed.

### Supported API Keys

| Environment Variable | Provider | Notes |
|----------------------|----------|-------|
| `ANTHROPIC_API_KEY` | Anthropic | Claude models |
| `OPENAI_API_KEY` | OpenAI | GPT models, standard API |
| `GEMINI_API_KEY` | Google | Gemini models (default: gemini-3.1-pro-preview) |
| `OPENROUTER_API_KEY` | OpenRouter | 100+ models gateway |
| `AZURE_OPENAI_API_KEY` | Azure | Requires additional env vars |

## OpenAI OAuth (ChatGPT Subscription)

Use your existing ChatGPT Plus/Pro account without an API key:

```bash
kollab --login openai
```

The terminal will display a verification code and open your browser. After authorizing, your tokens are stored and automatically refreshed. Tokens expire in about 8 days.

**Prerequisites**: In ChatGPT, go to Settings > Security and enable "Device code authorization" (sometimes called "Codex" toggle).

## Custom Profiles via Environment Variables

For more control than the default auto-detection, create named profiles using the `KOLLAB_{NAME}_{FIELD}` pattern:

```bash
# Local LLM via Ollama
KOLLAB_OLLAMA_PROVIDER=custom
KOLLAB_OLLAMA_BASE_URL=http://localhost:11434/v1
KOLLAB_OLLAMA_MODEL=llama3.3
kollab --profile ollama

# Azure OpenAI
KOLLAB_AZURE_PROVIDER=azure_openai
KOLLAB_AZURE_API_KEY="<your-azure-api-key>"
KOLLAB_AZURE_MODEL=gpt-5.4
KOLLAB_AZURE_AZURE_ENDPOINT=https://your-resource.openai.azure.com
kollab --profile azure

# Custom endpoint with auth
KOLLAB_CUSTOM_PROVIDER=custom
KOLLAB_CUSTOM_BASE_URL=https://api.example.com/v1
KOLLAB_CUSTOM_API_KEY="<your-api-key>"
KOLLAB_CUSTOM_MODEL=custom-model-name
kollab --profile custom
```

### Profile Fields

| Field | Description | Example |
|-------|-------------|---------|
| `PROVIDER` | Provider type | `openai`, `anthropic`, `custom`, `azure_openai`, `gemini` |
| `API_KEY` | API key | `<your-api-key>` |
| `BASE_URL` | Custom endpoint URL | `https://api.example.com/v1` |
| `MODEL` | Model name | `gpt-5.4`, `claude-sonnet-4-6` |
| `TEMPERATURE` | Sampling (0.0-2.0) | `0.7` |
| `MAX_TOKENS` | Response limit | `4096` |

### Saving Profiles

After creating a profile via env vars, save it to your config:

```bash
# Save to global config (~/.kollab/config.json)
kollab --profile myprofile --save

# Save to local project config (.kollab/config.json)
kollab --profile myprofile --save --local
```

Once saved, you can use `kollab --profile myprofile` without setting env vars each time.

## Pipe Mode

For scripting and automation, pipe mode reads from stdin, processes, and exits:

```bash
# Direct query
kollab "What is the capital of France?"

# From stdin (query becomes instruction)
echo "Explain this code" | kollab -p

# Stdin as context, query as instruction
cat document.txt | kollab "summarize this" -p

# With timeout
git diff | kollab "write commit message" -p --timeout 30s

# Timeout formats: 30s, 5min, 1h
kollab --timeout 1h "long analysis task"
```

In pipe mode, stdin content is treated as context and the optional query argument is treated as the instruction for processing that context.

## System Prompts

Use a custom system prompt file:

```bash
kollab --system-prompt my-prompt.md
```

System prompt files support dynamic content via `<trender>` tags:

```markdown
<!-- Project structure -->
<trender type="project_tree" max_depth="3" />

<!-- File listings -->
<trender type="file_list" pattern="**/*.py" />

<!-- File contents -->
<trender type="file_content" path="README.md" />
```

System prompts also resolve via environment variables:
- `KOLLAB_SYSTEM_PROMPT` - Direct string content
- `KOLLAB_SYSTEM_PROMPT_FILE` - Path to custom file

## CLI Flags

Common flags:

| Flag | Description |
|------|-------------|
| `--profile <name>` | Use specific LLM profile |
| `--agent <name>` | Use specific agent |
| `--login <provider>` | OAuth login (currently `openai`) |
| `--save` | Save auto-created profile |
| `--local` | Save profile to local config |
| `--simple` | Plain text output (no boxes/colors) |
| `--timeout <duration>` | Pipe mode timeout (30s, 5min, 1h) |
| `--reset-config` | Reset config to defaults |
| `--font-dir` | Print Nerd Fonts directory path |

Run `kollab --help` for the complete list including plugin-registered flags.

## Next Steps

- [Configuration Guide](configuration.md) - Profiles, config files, directories
- [Providers Guide](providers.md) - All supported providers and their setup
- `/profile` command - List, create, and switch profiles interactively
- `/permissions` command - Configure tool approval modes
- `/save` command - Export conversations
