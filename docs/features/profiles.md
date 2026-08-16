---
title: "Profiles"
created: 2026-02-24
modified: 2026-08-06
status: active
---
# Profiles

Profiles are named LLM configurations that define how you connect to AI providers. Each profile specifies the provider, model, API key, and parameters like temperature and max tokens.

## Profile Resolution Order

Profile activation follows this priority (highest to lowest):

1. CLI `--llm` flag (e.g., `kollab --llm openai-oauth`)
2. Persisted `active_profile` from config.json (set by `/llm` or `/config`)
3. OAuth profile auto-registration (openai-oauth from stored tokens)
4. Environment variable auto-detection (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)
5. Fallback to `default` profile

important: OAuth profiles are registered BEFORE env detection, so
`--llm openai-oauth` works even when no env vars are set.

### Persisted Active Profile

When you activate a loadout via `/llm` or save via `/config`, the active
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
kollab --llm work    # Enterprise Azure OpenAI
kollab --llm local   # Ollama on your machine
kollab --llm cheap   # Gemini Flash for quick tasks
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
kollab --llm work
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

## Switching and Presets: `/llm`

The `/profile` command was replaced by `/llm` (aliases: `/loadout`, `/ld`).
Provider connections are created once with `/setup`; `/llm` handles everything
after that — browsing models, switching, and parameterized presets
("loadouts"). See [loadouts.md](loadouts.md).

```
/llm                             # Browse and switch loadouts
/llm <name>                      # Activate a loadout by name
/llm new                         # Create a loadout (pre-filled form)
```

Azure OpenAI and fully-custom endpoints are added by hand under
`kollabor.llm.profiles` in `config.json` — see
[../providers.md](../providers.md).

## Environment Variable Pattern

Create profiles entirely from environment variables using the pattern:

```bash
KOLLAB_{NAME}_{FIELD}=value
```

### Valid Fields

| Field | Description | Example |
|-------|-------------|---------|
| `PROVIDER` | Provider type | `anthropic`, `openai`, `custom`, `azure_openai`, `gemini`, `openai_responses`, `openrouter` |
| `MODEL` | Model identifier | `claude-sonnet-5`, `gpt-5.6-terra` |
| `API_KEY` | Authentication key | `<your-anthropic-api-key>` |
| `BASE_URL` | Custom endpoint | `http://localhost:11434/v1` |
| `TEMPERATURE` | Sampling randomness | `0.7` (0.0-2.0; ignored on models that reject sampling params) |
| `MAX_TOKENS` | Response length limit | `4096` |
| `TIMEOUT` | Request timeout in seconds | `30` (0 = provider default) |
| `TOP_P` | Nucleus sampling | `0.9` (0.0-1.0) |
| `EFFORT` | Reasoning effort | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` (unset = model default) |
| `STREAMING` | Stream responses | `true` / `false` |
| `SUPPORTS_TOOLS` | Enable tool calling | `true` / `false` |

### Examples

#### Anthropic Claude (work)
```bash
export KOLLAB_WORK_PROVIDER=anthropic
export KOLLAB_WORK_API_KEY="<your-anthropic-api-key>"
export KOLLAB_WORK_MODEL=claude-sonnet-4-6   # accepts temperature
export KOLLAB_WORK_TEMPERATURE=0.5

kollab --llm work
```

#### Local Ollama
```bash
export KOLLAB_LOCAL_PROVIDER=custom
export KOLLAB_LOCAL_BASE_URL=http://localhost:11434/v1
export KOLLAB_LOCAL_MODEL=llama3.3
export KOLLAB_LOCAL_API_KEY=  # Empty for local

kollab --llm local
```

#### Azure OpenAI (enterprise)
```bash
export KOLLAB_ENTERPRISE_PROVIDER=azure_openai
export KOLLAB_ENTERPRISE_API_KEY=...
export KOLLAB_ENTERPRISE_MODEL=gpt-5.6-terra
export KOLLAB_ENTERPRISE_BASE_URL=https://your-resource.openai.azure.com
export KOLLAB_ENTERPRISE_API_VERSION=2025-01-01-preview

kollab --llm enterprise
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
/setup
# ... walk the wizard ...
kollab --llm work    # Profile saved to ~/.kollab/config.json
```

### Save to Project Config

Add the profile under `kollabor.llm.profiles` in `.kollab/config.json`
(project-specific).

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

At runtime, profile-specific and global `KOLLAB_*_API_KEY` / `KOLLAB_API_KEY`
environment variables override keys loaded from config (see resolution order in
`profile_manager`).

### API keys: environment vs saved profile (`--save` / `--default`)

When you run `kollab --llm <name> --save` or `--default`, Kollab writes the
profile into `config.json`. **API keys are not stored as cleartext in that file
when the OS keyring is available:** the secret is saved to your OS keychain
(service `kollab`, account = profile name), and config stores a sentinel value
`secret:keyring:<profile_name>` that points at it. Install `keyring` if your
platform needs it (`pip install keyring`).

If the key came from `KOLLAB_<PROFILE>_API_KEY` or `KOLLAB_API_KEY`, it is still
persisted to the keyring on save so the profile works **after you unset those env
vars**. If keyring storage fails, Kollab falls back to saving the key in
`config.json` and logs a warning.

Auto-detected profiles such as `anthropic-auto` (from `ANTHROPIC_API_KEY` alone)
still do **not** persist the provider API key into config; keep using the
provider env var or copy the profile to a named profile and save from `/config`.

### Persisting Active Profile

The active profile name is persisted when you:
- Activate a loadout via `/llm <name>`
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
kollab --llm openai-oauth

# Set as active profile
/llm openai-oauth

# Save to config for auto-activation on startup
kollab --llm openai-oauth --save
```

important: The profile is registered from stored tokens on every
startup. If tokens expire or are deleted, the profile won't be
registered and attempts to use it will fall back to "default".

## Switching Profiles at Runtime

Use `/llm` to switch without restarting:

```
/llm local
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
export KOLLAB_CHEAP_MODEL=gemini-3.6-flash
export KOLLAB_CHEAP_MAX_TOKENS=1024
```

Usage:
```bash
kollab --llm work    # Deep reasoning
kollab --llm local   # Private, offline
kollab --llm cheap   # Quick, low-cost
```

## CLI --llm Flag

Select a profile at startup:

```bash
kollab --llm my-profile     # Use a profile created via /setup or /llm
kollab --llm openai-oauth   # Use OAuth profile (requires --login first)
```

The `--llm` flag takes highest priority in the resolution order,
overriding persisted active_profile and all auto-detection.

Use `--default` with `--llm` to set startup default in config:

```bash
kollab --llm work --default           # set global default profile
kollab --llm work --default --local   # set project-local default profile
```

### Saving Auto-Detected Profiles

Combine `--llm` with `--save` to persist an auto-detected profile:

```bash
# Auto-detects from ANTHROPIC_API_KEY, saves as "anthropic-auto" profile
export ANTHROPIC_API_KEY="<your-anthropic-api-key>"
kollab --llm anthropic-auto --save

# Future runs use the saved profile
kollab --llm anthropic-auto
```

Use `--local` with `--save` to save to project config instead of global:

```bash
kollab --llm openai-auto --save --local
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

Only the fallback profile is built in. Provider-specific profiles are created
by `/setup` (or manually in `config.json`):

| Name | Provider | Model | Description |
|------|----------|-------|-------------|
| `default` | auto | (auto-detected) | Auto-detect from env vars, fallback to local LLM |

`openai-oauth` is registered only when OAuth tokens exist; it is not a built-in
provider template.

## Auto-Detected Profiles

When provider env vars are set, ephemeral auto-profiles are created:

| Env Var | Profile Name | Provider | Model |
|---------|--------------|----------|-------|
| `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | anthropic-auto | anthropic | claude-sonnet-5 |
| `OPENAI_API_KEY` | openai-auto | openai | gpt-5.6-terra |
| `AZURE_OPENAI_API_KEY` | azure-auto | azure_openai | gpt-5.6-terra |
| `GEMINI_API_KEY` | gemini-auto | gemini | gemini-3.6-flash |
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
