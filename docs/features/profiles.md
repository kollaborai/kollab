---
title: "Profiles"
created: 2026-02-24
modified: 2026-04-10
status: active
---
# Profiles

Profiles are named LLM configurations that define how you connect to AI providers. Each profile specifies the provider, model, API key, and parameters like temperature and max tokens.

## Profile Resolution Order

Profile activation follows this priority (highest to lowest):

1. CLI `--profile` flag (e.g., `kollab --profile openai-oauth`)
2. Persisted `active_profile` from config.json (set by `/profile set` or `/config`)
3. OAuth profile auto-registration (openai-oauth from stored tokens)
4. Environment variable auto-detection (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)
5. Fallback to `default` profile

important: OAuth profiles are registered BEFORE env detection, so
`--profile openai-oauth` works even when no env vars are set.

### Persisted Active Profile

When you use `/profile set <name>` or save via `/config`, the active
profile name is persisted to config.json. On startup, this persisted
value is restored. If the persisted profile isn't found (e.g., an
oauth profile before tokens are loaded), a WARN log is emitted and the
current active profile is used instead.

log example when persisted profile is missing:
  Persisted active profile 'openai-oauth' not found in registry;
  falling back to current active 'default'

## Why Profiles

Profiles let you switch between different AI setups without editing config files:

```bash
kollab --profile work    # Enterprise Azure OpenAI
kollab --profile local   # Ollama on your machine
kollab --profile cheap   # Gemini Flash for quick tasks
```

## Environment Variable Pattern

Create profiles entirely from environment variables using the pattern:

```bash
KOLLAB_{NAME}_{FIELD}=value
```

When an env var like `OPENAI_API_KEY` is set, you can reference it with:
```bash
export KOLLAB_WORK_PROVIDER=openai
export KOLLAB_WORK_API_KEY=$OPENAI_API_KEY
kollab --profile work
```

### Disabling Auto-Detection

Set `KOLLAB_NO_AUTO_DETECT=1` to skip all auto-detection:

```bash
export KOLLAB_NO_AUTO_DETECT=1
kollab  # Will use "default" profile only
```

Or set in config.json:
```json
{
  "kollabor": {
    "llm": {
      "auto_detect_provider": false
    }
  }
}
```

## The 'auto' Provider

Set `provider` to `auto` to let Kollabor pick the best available provider based on your API keys:

```bash
KOLLAB_MYPROFILE_PROVIDER=auto
KOLLAB_MYPROFILE_API_KEY="<your-anthropic-api-key>"
```

Detection order:
1. Anthropic (keys starting with `sk-ant-`)
2. OpenAI (keys starting with `sk-`)
3. Falls back to OpenAI if unknown format

## Profile Command

The `/profile` command (aliases: `/prof`, `/llm`) manages profiles interactively:

```
/profile list                    # List all profiles with details
/profile set <name>              # Switch active profile
/profile create                  # Create new profile (interactive wizard)
```

note: `show` and `delete` subcommands are not implemented.

### Creating Profiles Interactively

```
/profile create
```

Prompts you for:
1. Profile name (e.g., `work-azure`)
2. Provider (auto, openai, anthropic, azure_openai, gemini, openrouter, custom)
3. API key (masked input, shown as `<your-api-key>-xyz`)
4. Model name
5. Temperature (0.0-2.0)
6. Max tokens
7. Base URL (for custom providers)
8. Organization ID (OpenAI only)

After basic setup, you can configure advanced settings:
- Description
- Timeout (milliseconds, 0 = no timeout)
- Tool calling support
- Streaming enabled

## Environment Variable Pattern

Create profiles entirely from environment variables using the pattern:

```bash
KOLLAB_{NAME}_{FIELD}=value
```

### Valid Fields

| Field | Description | Example |
|-------|-------------|---------|
| `PROVIDER` | Provider type | `anthropic`, `openai`, `custom`, `azure_openai`, `gemini`, `openai_responses`, `openrouter` |
| `MODEL` | Model identifier | `claude-sonnet-4-6`, `gpt-5.4` |
| `API_KEY` | Authentication key | `<your-anthropic-api-key>` |
| `BASE_URL` | Custom endpoint | `http://localhost:11434/v1` |
| `TEMPERATURE` | Sampling randomness | `0.7` (0.0-2.0) |
| `MAX_TOKENS` | Response length limit | `4096` |
| `TIMEOUT` | Request timeout | `30000` (milliseconds, 0 = none) |
| `TOP_P` | Nucleus sampling | `0.9` (0.0-1.0) |
| `STREAMING` | Stream responses | `true` / `false` |
| `SUPPORTS_TOOLS` | Enable tool calling | `true` / `false` |

### Examples

#### Anthropic Claude (work)
```bash
export KOLLAB_WORK_PROVIDER=anthropic
export KOLLAB_WORK_API_KEY="<your-anthropic-api-key>"
export KOLLAB_WORK_MODEL=claude-sonnet-4-6
export KOLLAB_WORK_TEMPERATURE=0.5

kollab --profile work
```

#### Local Ollama
```bash
export KOLLAB_LOCAL_PROVIDER=custom
export KOLLAB_LOCAL_BASE_URL=http://localhost:11434/v1
export KOLLAB_LOCAL_MODEL=llama3.3
export KOLLAB_LOCAL_API_KEY=  # Empty for local

kollab --profile local
```

#### Azure OpenAI (enterprise)
```bash
export KOLLAB_ENTERPRISE_PROVIDER=azure_openai
export KOLLAB_ENTERPRISE_API_KEY=...
export KOLLAB_ENTERPRISE_MODEL=gpt-5.4
export KOLLAB_ENTERPRISE_BASE_URL=https://your-resource.openai.azure.com
export KOLLAB_ENTERPRISE_API_VERSION=2025-01-01-preview

kollab --profile enterprise
```

### Global Overrides

Set `KOLLAB_{FIELD}` (without profile name) to override any active profile:

```bash
export KOLLAB_MODEL=claude-opus-4-6   # Overrides model for all profiles
export KOLLAB_TEMPERATURE=0.3        # Overrides temperature
```

## Profile Priority

Values resolve in this order (highest to lowest):

1. `KOLLAB_{PROFILE_NAME}_{FIELD}` - Profile-specific env var
2. `KOLLAB_{FIELD}` - Global env var override
3. Config file value (`config.json`)
4. Default value

Example for model field with profile named `work`:
1. `KOLLAB_WORK_MODEL` (checked first)
2. `KOLLAB_MODEL` (checked second)
3. `config.json` work.profile.model
4. Default model for provider

## Saving Profiles

### Save to Global Config
```bash
/profile create
# ... fill in wizard ...
kollab --profile work    # Profile saved to ~/.kollab/config.json
```

### Save to Project Config
```bash
/profile create --local
# Profile saved to .kollab/config.json (project-specific)
```

### Store API Key in Config

By default, API keys from env vars are used at runtime. To store directly in config:

```json
{
  "kollabor": {
    "llm": {
      "profiles": {
        "work": {
          "provider": "anthropic",
          "model": "claude-sonnet-4-6",
          "api_key": "<your-anthropic-api-key>",
          "temperature": 0.5
        }
      }
    }
  }
}
```

Stored keys take precedence over env vars for that profile.

### Persisting Active Profile

The active profile name is persisted when you:
- Use `/profile set <name>`
- Edit config via `/config` and modify `kollabor.llm.active_profile`

On startup, this persisted value is restored. This survives restarts
and works for oauth profiles like `openai-oauth` (phase 4.5 fix).

log example when persisted profile is activated:
  Activated persisted profile: openai-oauth

## OpenAI OAuth

Use your ChatGPT Plus/Pro subscription without an API key:

```bash
kollab --login openai
```

### Setup

In ChatGPT web:
1. Go to Settings > Security (or Data Controls)
2. Enable "Device code authorization"

### How It Works

1. CLI displays a verification code
2. Browser opens to `auth.openai.com/codex/device`
3. Enter the code to authorize
4. Tokens stored at `~/.kollab/oauth/openai.json`

The `openai-oauth` profile is auto-registered at startup when valid
tokens exist. This profile uses the `openai_responses` provider with
base_url pointing to ChatGPT's codex backend (not api.openai.com).

### Token Management

- Tokens expire in ~8 days
- Auto-refresh on expiry
- Re-run `kollab --login openai` if refresh fails

### Using the OAuth Profile

Once logged in, the profile is available immediately:

```bash
# Explicit selection (works now, was broken before phase 4.5)
kollab --profile openai-oauth

# Set as active profile
/profile set openai-oauth

# Save to config for auto-activation on startup
kollab --profile openai-oauth --save openai-oauth
```

important: The profile is registered from stored tokens on every
startup. If tokens expire or are deleted, the profile won't be
registered and attempts to use it will fall back to "default".

## Switching Profiles at Runtime

Use `/profile set` to switch without restarting:

```
/profile set local
```

Profile switch takes effect immediately for the next message.

## Complete Example: Three Profiles

Set up work, local, and cheap profiles:

```bash
# ~/.zshrc or ~/.bashrc

# Work: Claude Sonnet for complex tasks
export KOLLAB_WORK_PROVIDER=anthropic
export KOLLAB_WORK_API_KEY="<your-anthropic-api-key>"
export KOLLAB_WORK_MODEL=claude-sonnet-4-6
export KOLLAB_WORK_MAX_TOKENS=8192

# Local: Ollama for privacy
export KOLLAB_LOCAL_PROVIDER=custom
export KOLLAB_LOCAL_BASE_URL=http://localhost:11434/v1
export KOLLAB_LOCAL_MODEL=llama3.3

# Cheap: Gemini for quick tasks
export KOLLAB_CHEAP_PROVIDER=gemini
export KOLLAB_CHEAP_API_KEY=...
export KOLLAB_CHEAP_MODEL=gemini-3.1-pro-preview
export KOLLAB_CHEAP_MAX_TOKENS=1024
```

Usage:
```bash
kollab --profile work    # Deep reasoning
kollab --profile local   # Private, offline
kollab --profile cheap   # Quick, low-cost
```

## CLI --profile Flag

Select a profile at startup:

```bash
kollab --profile claude         # Use built-in claude profile
kollab --profile openai         # Use built-in openai profile
kollab --profile openai-oauth   # Use OAuth profile (requires --login first)
kollab --profile local          # Use local LLM profile
```

The `--profile` flag takes highest priority in the resolution order,
overriding persisted active_profile and all auto-detection.

### Saving Auto-Detected Profiles

Combine `--profile` with `--save` to persist an auto-detected profile:

```bash
# Auto-detects from ANTHROPIC_API_KEY, saves as "work" profile
export ANTHROPIC_API_KEY="<your-anthropic-api-key>"
kollab --profile anthropic-auto --save work

# Future runs use the saved profile
kollab --profile work
```

Use `--local` with `--save` to save to project config instead of global:

```bash
kollab --profile openai-auto --save team --local
# Saved to .kollab/config.json (project-specific)
```

## Configuration Structure

Profiles are stored in `config.json`:

```json
{
  "kollabor": {
    "llm": {
      "profiles": {
        "my-profile": {
          "provider": "anthropic",
          "model": "claude-sonnet-4-6",
          "api_key": "",
          "base_url": "",
          "temperature": 0.7,
          "max_tokens": 4096,
          "timeout": 0,
          "top_p": null,
          "streaming": true,
          "supports_tools": true,
          "description": "My custom profile"
        }
      },
      "active_profile": "my-profile"
    }
  }
}
```

## Built-in Profiles

These profiles are available by default:

| Name | Provider | Model | Description |
|------|----------|-------|-------------|
| `default` | auto | (auto-detected) | Auto-detect from env vars, fallback to local LLM |
| `local` | custom | qwen3-4b | Local LLM via LM Studio / Ollama |
| `claude` | anthropic | claude-sonnet-4-6 | Anthropic Claude for complex tasks |
| `openai` | openai | gpt-5.4 | OpenAI GPT-5.4 for general tasks |
| `openai-oauth` | openai_responses | gpt-5.4 | OpenAI OAuth (ChatGPT account), auto-registered when tokens exist |

## Auto-Detected Profiles

When provider env vars are set, ephemeral auto-profiles are created:

| Env Var | Profile Name | Provider | Model |
|---------|--------------|----------|-------|
| `ANTHROPIC_API_KEY` | anthropic-auto | anthropic | claude-sonnet-4-6 |
| `OPENAI_API_KEY` | openai-auto | openai | gpt-5.4 |
| `AZURE_OPENAI_API_KEY` | azure-auto | azure_openai | gpt-5.4 |
| `GEMINI_API_KEY` | gemini-auto | gemini | gemini-3.1-pro-preview |
| `OPENROUTER_API_KEY` | openrouter-auto | openrouter | deepseek/deepseek-v3.2 |
| `XAI_API_KEY` | xai-auto | custom | grok-4-1-fast-reasoning |
| `ZAI_API_KEY` | zai-auto | custom | glm-5 |
| `MOONSHOT_API_KEY` | kimi-auto | custom | kimi-k2.5 |

For full config file details, see [configuration.md](../configuration.md).

## Provider-Specific Details

Each provider has unique configuration options. See [providers.md](../providers.md) for:

- API endpoints
- Supported models
- Provider-specific fields (Azure endpoint, OpenRouter headers)
- OAuth setup
- Custom endpoint configuration
