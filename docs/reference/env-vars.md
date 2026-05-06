---
title: "Kollab Environment Variables"
created: 2026-04-21
modified: 2026-04-21
status: active
---
# Kollab Environment Variables

Complete reference for all `KOLLAB_*` environment variables read by the
app. Most users will never touch these — they exist for CI, multi-agent
deployments, security posture changes, and debugging.

See also:
- `docs/reference/commands.md` — CLI flags and slash commands
- `CLAUDE.md` — architecture and config system
- `docs/providers/` — provider-specific setup


## User-facing

Variables you might actually want to set.

### KOLLAB_COLOR_MODE

Terminal color mode override. Auto-detected by default.

Accepted values (case-insensitive):
- True color: `truecolor`, `24bit`, `true`
- 256 color: `256`, `256color`, `extended`
- 16 color: `16`, `basic`
- Monochrome: `none`, `off`, `no`

```bash
KOLLAB_COLOR_MODE=256 kollab
export KOLLAB_COLOR_MODE=truecolor
```

Use `none` on dumb terminals, `256` on Apple Terminal, `truecolor` on
modern terminals (iTerm2, Ghostty, etc). See CLAUDE.md → "Terminal Color
Support" for detection order.

### KOLLAB_SYSTEM_PROMPT

Override the system prompt with a literal string. Takes precedence over
`--system-prompt` flag, agent bundles, and config.

```bash
KOLLAB_SYSTEM_PROMPT="you are a terse bash expert" kollab
```

### KOLLAB_SYSTEM_PROMPT_FILE

Path to a system prompt file. Used if `KOLLAB_SYSTEM_PROMPT` is not set.

```bash
KOLLAB_SYSTEM_PROMPT_FILE=~/prompts/devops.md kollab
```

Priority: `--system-prompt` arg > `KOLLAB_SYSTEM_PROMPT` env > `KOLLAB_SYSTEM_PROMPT_FILE` env > agent bundle > built-in.

### KOLLAB_NO_AUTO_DETECT

Disable auto-detection of LLM provider from existing env vars
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.).

```bash
KOLLAB_NO_AUTO_DETECT=1 kollab --profile my-custom-profile
```

Useful when you have both provider env vars set AND want to force a
specific named profile.

### KOLLAB_PROJECT_ROOT

Override the project root used for hub siloing and conversation storage.
Defaults to git root or cwd. Normally set by `--project` flag, but can be
set directly.

```bash
KOLLAB_PROJECT_ROOT=/path/to/project kollab
```

Path is encoded the same way as `~/.kollab/projects/<encoded-path>/`.

### KOLLAB_HUB_PROJECT_SCOPED

Silo hub state (presence, vaults, sockets) under the current project
instead of the global `~/.kollab/hub/`. This is enabled by default.

```bash
KOLLAB_HUB_PROJECT_SCOPED=1 kollab --agent coder --as lapis
```

Typically set automatically when running inside a project directory. Set
manually only when debugging or forcing behavior in a script.

Values: `1`, `true`, `yes`, `on` (truthy)


## Profile overrides

Kollab LLM profiles can be fully configured via environment variables.
Two patterns:

**Profile-specific** — targets one named profile:
```
KOLLAB_{PROFILE_NAME}_{FIELD}
```

**Global** — applies to the active profile:
```
KOLLAB_{FIELD}
```

Profile names are normalized: lowercased, non-alphanumeric → `_`, then
uppercased. Examples:
- `my-local-llm` → `KOLLAB_MY_LOCAL_LLM_*`
- `my.profile` → `KOLLAB_MY_PROFILE_*`
- `  fast  ` → `KOLLAB_FAST_*`

### Profile fields

| Field | Type | Description |
|-------|------|-------------|
| `API_KEY` | string | API key/token |
| `MODEL` | string | Model name (required for auto-created profiles) |
| `PROVIDER` | string | Provider type (default: `custom`) |
| `BASE_URL` | string | API endpoint URL |
| `MAX_TOKENS` | integer | Max response tokens |
| `TEMPERATURE` | float | Sampling temperature (0.0–2.0) |
| `TIMEOUT` | integer | Request timeout in ms |
| `TOP_P` | float | Nucleus sampling (0.0–1.0) |
| `STREAMING` | bool | Enable streaming (`true`/`false`) |
| `SUPPORTS_TOOLS` | bool | Enable tool calling (`true`/`false`) |
| `DESCRIPTION` | string | Human-readable description |

### Examples

```bash
# Override the active profile's model globally
KOLLAB_MODEL=gpt-5.4 kollab

# Profile-specific: create a "fast" profile from env
KOLLAB_FAST_MODEL=claude-haiku-4-5 \
KOLLAB_FAST_PROVIDER=anthropic \
KOLLAB_FAST_API_KEY="<your-anthropic-api-key>" \
kollab --profile fast

# Persist the auto-created profile to config
kollab --profile fast --save              # global
kollab --profile fast --save --local      # project-local

# Multiple fields at once
KOLLAB_MAX_TOKENS=8192 \
KOLLAB_TEMPERATURE=0.3 \
kollab
```

### Fallback chain

For any field, lookup order is:
1. `KOLLAB_{PROFILE}_{FIELD}` (profile-specific env)
2. `KOLLAB_{FIELD}` (global env)
3. Profile config value
4. Built-in default

### Provider-specific aliases

Some providers have legacy env var names that still work:

- `KOLLAB_CLAUDE_API_KEY`, `KOLLAB_CLAUDE_TOKEN` — Anthropic key
- `KOLLAB_API_KEY` — generic fallback

Auto-detected provider profiles can also read provider-native model overrides:

| API key env var | Model env var |
|-----------------|---------------|
| `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` |
| `OPENAI_API_KEY` | `OPENAI_MODEL` |
| `AZURE_OPENAI_API_KEY` | `AZURE_OPENAI_MODEL` |
| `GEMINI_API_KEY` | `GEMINI_MODEL` |
| `OPENROUTER_API_KEY` | `OPENROUTER_MODEL` |
| `XAI_API_KEY` | `XAI_MODEL` |
| `ZAI_API_KEY` | `ZAI_MODEL` |
| `MOONSHOT_API_KEY` | `MOONSHOT_MODEL` |

Example:

```bash
export OPENROUTER_API_KEY="<your-openrouter-api-key>"
export OPENROUTER_MODEL="deepseek/deepseek-v3.2"
kollab
```

Check `docs/providers/` for each provider's full env var list.


## Security-sensitive

Read these carefully. Setting them wrong weakens your security posture.

### KOLLAB_ALLOW_PLAINTEXT_KEYS

Store API keys as plaintext in `config.json` instead of the encrypted
keystore. Disables Tier 4 key protection.

```bash
KOLLAB_ALLOW_PLAINTEXT_KEYS=true kollab   # only for dev/CI
```

Only set this when:
- Running in a sandboxed CI environment
- The `keyring` library can't be installed
- You understand keys will be readable from disk

Never set this on a developer workstation or shared machine. Prefer
`pip install keyring` and let the OS keychain handle encryption.

### KOLLAB_KEY_ENCRYPTION_PASSWORD

Password for the fernet-encrypted keystore (Tier 2 protection, used when
OS keychain is unavailable).

```bash
KOLLAB_KEY_ENCRYPTION_PASSWORD="<your-key-encryption-password>" kollab
```

If you lose this password after keys are encrypted, the only recovery is
deleting the keystore file and re-entering keys.

### KOLLAB_ALLOWED_API_HOSTS

Extend the built-in API host allowlist with additional hosts. Comma-
separated. This is an **addition** to the default list, not a replacement.

```bash
KOLLAB_ALLOWED_API_HOSTS="my-proxy.example.com,internal-llm.corp"
```

Use when routing API calls through a corporate proxy or running a
self-hosted LLM behind a custom domain.

### KOLLAB_ENGINE_BYPASS_AUTH

**DANGEROUS.** Disables authentication on the kollabor-engine server.
Allows any local process (or network client if bound to non-localhost)
to connect without credentials.

```bash
KOLLAB_ENGINE_BYPASS_AUTH=1   # never in production
```

Set only in tests or trusted single-user dev loops. Never expose an
auth-bypassed engine to a network interface.


## Hub bridge (Telegram)

Alternatives to interactive setup (`/hub bridge setup`).

### KOLLAB_HUB_BRIDGE_TOKEN

Telegram bot token from `@BotFather`.

### KOLLAB_HUB_BRIDGE_CHAT_ID

Telegram chat ID from `@userinfobot`.

```bash
export KOLLAB_HUB_BRIDGE_TOKEN="<your-telegram-bot-token>"
export KOLLAB_HUB_BRIDGE_CHAT_ID=-100123456789
kollab --agent koordinator
```

Interactive setup is easier for first-time use. Env vars make sense for
deployed/detached agents.


## Hub DNS (experimental)

### KOLLAB_WELL_KNOWN_RSYNC

Rsync destination for publishing the hub's `well-known/agent-keys` file
to an external server. When set, the coordinator syncs after every
update. When unset, publishing is skipped entirely.

Format: standard rsync destination string.

```bash
export KOLLAB_WELL_KNOWN_RSYNC="user@host:~/.kollab/hub/dns/well-known/"
```

Fire-and-forget: failures are logged but non-fatal. Experimental — the
DNS layer is still evolving and this var may be replaced or renamed.


## Dev-only (not in pip package)

These packages are in the monorepo but not distributed via `pip install
kollab`. Only relevant if you're running from source with the full
workspace.

### KOLLAB_WEBUI_PORT

Port for the `kollabor-webui` dev server.

Default: `8080`

### KOLLAB_ENGINE_URL

URL of the `kollabor-engine` server that the webui connects to.

Default: `http://127.0.0.1:7433`


## Internal (do not set manually)

These are set by the app itself to coordinate daemon forking, IPC, and
spawn context. Setting them manually will confuse the runtime. Listed
here so you know to leave them alone.

| Variable | Purpose |
|----------|---------|
| `KOLLAB_DAEMON_PID` | PID of forked daemon — set by `--daemon` path |
| `KOLLAB_DAEMON_READY_FD` | Pipe FD for daemon-ready signaling |
| `KOLLAB_PARENT_PID` | Parent watchdog — child self-terminates if parent dies |
| `KOLLAB_ROOT_SOCKET` | Socket namespace for hub coordination |
| `KOLLAB_AGENT_ID` | Agent ID inherited by spawned sub-agents |
| `KOLLAB_AGENT_NAME` | Agent name inherited by spawned sub-agents |
| `KOLLAB_DEEP_THOUGHT_INSTANCE` | Deep-thought plugin instance marker |
| `KOLLAB_DEEP_THOUGHT_CHILD` | Deep-thought child process flag |
| `KOLLAB_DEEP_THOUGHT_SOCKET` | Deep-thought IPC socket path |


## Deprecated / aliases

None currently. History:

- `KOLLAB_DESIGNATION` → use `--as` CLI flag (renamed to "identity" in
  commit 3193622, 2026-04-06)


## Debugging env var lookup

List all `KOLLAB_*` vars currently set in your shell:

```bash
env | grep ^KOLLAB_
```

To trace which env vars kollabor actually reads at startup, use the
platform's syscall tracer:

```bash
# macOS
sudo dtruss -f kollab 2>&1 | grep KOLLAB

# Linux
strace -e openat,access -f kollab 2>&1 | grep KOLLAB
```
