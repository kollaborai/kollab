---
title: "Secrets Management Spec"
created: 2026-04-07
modified: 2026-04-07
status: draft
---
# Secrets Management Spec

status: draft
author: maintainers
date: 2026-04-07

## problem

sensitive values (api keys, bot tokens, passwords) are stored in
plaintext in ~/.kollab/config.json. the security infrastructure
exists in kollabor_ai/providers/security.py (4-tier keyring system,
AES-256-GCM encrypted file, env vars, plaintext fallback) but it's
dead code -- never wired into the actual config read/write path.

plugins like hub store tokens in config.json:
  plugins.hub.bridge_token = "<your-telegram-bot-token>"

llm profiles store api keys in config.json:
  kollabor.llm.profiles.claude.api_key = "<your-anthropic-api-key>"

profile_manager.py has env var resolution (KOLLAB_CLAUDE_API_KEY,
ANTHROPIC_API_KEY, etc) but that only covers LLM profiles, not
plugin secrets.

## goal

any config value marked as sensitive never touches disk in plaintext.
zero friction for users -- secrets are stored/retrieved transparently.
works in all environments (desktop, headless, docker, CI).

## design

### secret resolution order (per value)

  1. env var (highest priority, always wins)
  2. OS keyring (macOS Keychain, Linux Secret Service, Windows DPAPI)
  3. encrypted file (~/.kollab/secrets.enc, AES-256-GCM)
  4. config.json plaintext (legacy only, auto-migrated on first read)

this matches what security.py already implements. we just need to
wire it into the config system so it's universal.

### config field metadata: marking fields as sensitive

plugins and core config already declare their schemas via
get_default_config() and get_config_widgets(). add a "sensitive"
flag to widget definitions:

  {
    "type": "text_input",
    "label": "Bot Token",
    "config_path": "plugins.hub.bridge_token",
    "sensitive": true,
    "env_var": "KOLLAB_HUB_BRIDGE_TOKEN",
    "help": "Telegram bot token for bridge"
  }

sensitive: true means:
  - value is stored in keyring/encrypted file, NOT config.json
  - config.json stores a sentinel: "secret:keyring:<key_id>"
  - /config modal masks the value (shows ************)
  - value is redacted in logs (already handled by LoggingRedactor)
  - /config modal shows env var hint when field is empty

### sentinel format in config.json

when a secret is stored, config.json gets a reference pointer:

  "bridge_token": "secret:keyring:plugins.hub.bridge_token"

or if keyring unavailable:

  "bridge_token": "secret:encrypted:plugins.hub.bridge_token"

on read, the config system sees the "secret:" prefix and resolves
it through the secret backend. if the prefix is missing (legacy
plaintext value), it auto-migrates.

### core component: SecretStore

new file: packages/kollabor-config/src/kollabor_config/secret_store.py

wraps the existing security.py classes into a single interface that
the config system calls.

  class SecretStore:
      """Unified secret storage with tier fallback."""

      def __init__(self, config_dir: Path):
          # try to init keyring (tier 1)
          # try to init encrypted file (tier 2)
          # env var lookup is always available (tier 3)
          # track which backend is active

      async def store(self, key: str, value: str) -> str:
          """Store secret, return sentinel string for config.json."""
          # try keyring -> encrypted -> fail
          # return "secret:keyring:<key>" or "secret:encrypted:<key>"

      async def retrieve(self, sentinel: str) -> Optional[str]:
          """Resolve sentinel or env var to actual value."""
          # parse sentinel format
          # try the indicated backend
          # fallback through tiers if primary fails

      async def retrieve_or_env(self, key: str, env_var: str,
                                 sentinel: Optional[str]) -> Optional[str]:
          """Full resolution: env -> sentinel -> None."""

      async def delete(self, key: str) -> bool:
          """Remove from all backends."""

      async def migrate_plaintext(self, key: str, value: str) -> str:
          """Move plaintext value to secure storage, return sentinel."""

      def get_backend_info(self) -> dict:
          """Report which backend is active (for /config display)."""

### config system integration

ConfigService.get() and ConfigService.set() are the chokepoints.

  ConfigService.get(path, default):
    value = <normal config lookup>
    if isinstance(value, str) and value.startswith("secret:"):
        # check env var first (from widget metadata)
        env_var = self._get_env_var_for_path(path)
        if env_var and os.environ.get(env_var):
            return os.environ[env_var]
        # resolve from secret store
        return await self._secret_store.retrieve(value)
    return value

  ConfigService.set(path, value):
    if self._is_sensitive_path(path):
        sentinel = await self._secret_store.store(path, value)
        <normal config set>(path, sentinel)
    else:
        <normal config set>(path, value)

_is_sensitive_path() checks against a registry of sensitive paths,
populated from widget definitions (sensitive: true).

problem: ConfigService.get() is sync. secret store backends
(keyring, encrypted file) should be sync-safe on desktop but
may need asyncio on some platforms. solution:

  - keyring library calls are actually sync (they call C APIs)
  - encrypted file I/O is sync
  - wrap in sync methods, keep async interface for future-proofing
  - SecretStore has both sync get_sync() and async get() methods
  - ConfigService.get() calls get_sync()
  - async callers can use get() directly

### sensitive path registry

built at startup from widget definitions:

  SENSITIVE_PATHS = set()

  for plugin in discovered_plugins:
      widgets = plugin.get_config_widgets()
      for widget in widgets["widgets"]:
          if widget.get("sensitive"):
              SENSITIVE_PATHS.add(widget["config_path"])

  # core sensitive paths (hardcoded)
  SENSITIVE_PATHS.update([
      "kollabor.llm.profiles.*.api_key",
  ])

glob patterns (*.api_key) supported for dynamic profile names.

### env var convention

for plugin secrets, auto-generate env var names:

  config path                        env var
  plugins.hub.bridge_token      ->   KOLLAB_HUB_BRIDGE_TOKEN
  plugins.hub.api_key           ->   KOLLAB_HUB_API_KEY
  kollabor.llm.profiles.X.api_key -> KOLLAB_X_API_KEY (existing)

rule: KOLLAB_ + path segments (skip "plugins"/"kollabor") + UPPER

plugins can override with explicit env_var in widget definition.

### /config modal changes

  1. sensitive text_input widgets show:
     - masked value: "************" (or empty if no value)
     - "[keyring]" or "[env]" or "[encrypted]" badge showing source
     - env var hint: "or set KOLLAB_HUB_BRIDGE_TOKEN"

  2. editing a sensitive field:
     - clears the mask, allows typing new value
     - on save, routes through SecretStore.store()
     - config.json gets sentinel, not the value

  3. new widget type not needed -- reuse text_input with sensitive flag

### auto-migration on startup

during ConfigService initialization:

  for path in SENSITIVE_PATHS:
      value = raw_config_get(path)  # read config.json directly
      if value and not value.startswith("secret:"):
          # plaintext secret found, migrate it
          sentinel = secret_store.migrate_plaintext(path, value)
          raw_config_set(path, sentinel)
          save_config()
          log.info(f"migrated {path} to secure storage")

this means existing users get auto-migrated on first run after
this feature ships. their config.json values get replaced with
sentinel strings, actual values move to keyring.

### /secrets command

new slash command for managing secrets directly:

  /secrets                 show all stored secrets (masked)
  /secrets show <path>     show secret source (keyring/env/encrypted)
  /secrets set <path>      prompt for new value
  /secrets delete <path>   remove from all backends
  /secrets migrate         force re-migration of any plaintext values
  /secrets backend         show which backend is active

### error handling

  - keyring locked/unavailable: fall through to encrypted file
  - encrypted file password missing: prompt user or fall through to env
  - no backend available: warn user, leave in plaintext with log warning
  - corrupted encrypted file: warn, don't crash, offer re-encryption
  - migration failure: leave original value, log error, retry next startup

### what this does NOT cover

  - secret rotation (out of scope)
  - multi-user / shared secrets (out of scope)
  - vault integrations like HashiCorp Vault (future)
  - SSH keys or certificates (different system)

## implementation plan

  phase 1: wire up SecretStore
    - create secret_store.py in kollabor-config
    - wrap existing security.py classes
    - add sync + async interfaces
    - unit tests

  phase 2: config integration
    - add sensitive flag to widget schema
    - build sensitive path registry
    - modify ConfigService.get/set for sentinel resolution
    - auto-migration on startup
    - unit tests

  phase 3: /config modal
    - masked display for sensitive fields
    - source badge ([keyring], [env], [encrypted])
    - env var hints
    - save routing through SecretStore

  phase 4: /secrets command
    - command registration
    - subcommand handlers
    - backend status display

  phase 5: mark existing secrets
    - add sensitive: true to hub bridge_token widget
    - add sensitive: true to any plugin with tokens
    - add sensitive: true to LLM profile api_key fields
    - test migration flow end-to-end

## files touched

  new:
    packages/kollabor-config/src/kollabor_config/secret_store.py
    kollabor/commands/secrets_command.py
    tests/unit/test_secret_store.py
    tests/tmux/specs/secrets_management.json

  modified:
    packages/kollabor-config/src/kollabor_config/service.py (get/set)
    packages/kollabor-config/src/kollabor_config/loader.py (migration)
    packages/kollabor-tui/src/kollabor_tui/config_widgets.py (masking)
    plugins/altview/config_altview.py (save routing)
    plugins/hub/plugin.py (add sensitive: true to widgets)
    kollabor/commands/registry.py (register /secrets)

  leveraged (existing, not modified):
    packages/kollabor-ai/src/kollabor_ai/providers/security.py
    packages/kollabor-ai/src/kollabor_ai/profile_manager.py
