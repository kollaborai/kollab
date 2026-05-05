---
name: kollabor-harness
description: "Self-awareness of the Kollab harness. Use when the user asks to extend, configure, customize, hook, or add a plugin/command/setting to Kollabor — or when asking how their current environment works. Covers config (global + local + dot-notation), event bus + hooks (python + JSON), plugin SDK, slash commands, unified tool pipeline, permissions, stdout rules, dynamic system prompts, and testing."
---

# kollabor-harness: know the ground you stand on

you are running inside **Kollab** — a terminal AI chat harness where
everything is an event and everything has a hook. this skill tells you how
the harness works so you can change it from inside it.

**when to use this skill:**

- user wants to add a hook, plugin, slash command, or setting
- user wants to configure something ("how do i make it..." / "can we add...")
- user wants to hook into a lifecycle event (pre-tool, post-response, etc.)
- user asks how kollabor works, what plugins are running, what they can extend
- you need to know whether a behavior is config-driven or code-driven
- you need the legal way to do something (render state, stdout, commits)

**when NOT to use:**

- generic python / shell / unrelated tasks
- debugging unrelated codebases
- the user is asking about kollabor.ai (the parent site) or kowork (web app)

## the one-line model

`EventBus` at the center. everything produces events. everything listens via
hooks registered at priorities (SYSTEM > SECURITY > PREPROCESSING > LLM >
POSTPROCESSING > DISPLAY). plugins layer on without touching core.

## configuration system

### locations

```
~/.kollab/config.json          global user config
.kollab/config.json            project config (merged on top of global)
~/.kollab/agents/              global agent bundles
.kollab/agents/                local agent bundles (override global)
~/.kollab/hooks.json           global JSON hooks
.kollab/hooks.json             project JSON hooks (appended after global)
~/.kollab/mcp/mcp_settings.json   MCP server config
~/.kollab/pricing.json         user pricing overrides (merged after bundled defaults)
~/.kollab/projects/<encoded-path>/    per-project conversations + logs
```

path encoding: `/home/user/proj` -> `home_user_proj`.

### access pattern

dot-notation everywhere. never reach into raw dicts.

```python
value = config.get("kollabor.llm.max_history", 90)
config.set("kollabor.ui.theme", "dark")
```

key prefix is `kollabor.*` (older code may use `core.*` — migration in progress).

### /config modal

`/config` opens fullscreen editor.
- `/` activates search (label, help, config path)
- `Enter` locks filter and navigates
- `Ctrl+S` saves — prompts Local (L) or Global (G)
- only diffs from defaults are persisted, never the full tree

### critical rule

**never edit `config.json` directly for cleanup.** the app regenerates values
from code at init time. always fix the source that generates the value. verify
what consumes the data before removing it.

### pricing registry

cost is calculated per-turn via `kollabor_ai.cost_calculator.calculate_cost`.
pricing comes from `PricingRegistry` (singleton) which loads in this order:

1. bundled: `packages/kollabor-ai/src/kollabor_ai/default_pricing.json`
2. user override: `~/.kollab/pricing.json` (merged on top)
3. runtime: providers may call `register_provider_pricing()` (e.g., openrouter
   fetches pricing from its `/api/v1/models` endpoint and registers each model)

format (same for bundled and user override) — **per-million rates**, matching
how providers publish pricing:

```json
{
  "custom": {
    "GLM-5.1": {
      "prompt_per_million":     0.60,
      "completion_per_million": 2.20,
      "cache_read_per_million": 0.11
    }
  },
  "anthropic": {
    "claude-sonnet-4-6": {
      "prompt_per_million":     3.00,
      "completion_per_million": 15.00,
      "cache_read_per_million": 0.30
    }
  }
}
```

- `prompt_per_million` and `completion_per_million` are **required**
- `cache_read_per_million` is optional (defaults to 10% of prompt rate)
- loader converts to per-token internally; `ModelPricing.cache_discount` is
  computed as `cache_read / prompt`
- for models that don't support caching, omit `cache_read_per_million`

provider family affects the math:

- **openai family** (`openai`, `openai_responses`, `azure_openai`, `custom`,
  `openrouter`): `prompt_tokens` INCLUDES `cache_read_tokens` as a subset →
  subtract to avoid double-billing
- **anthropic, gemini**: `prompt_tokens` already excludes cache_read (it's
  reported separately) → add the cache portion on top
- **unknown providers**: cost = 0.0

lookup order in `PricingRegistry.get_pricing(provider_type, model_id)`:

1. exact match: `openai/gpt-4o` in the openai table
2. openrouter namespace strip: `openai/gpt-4o` -> `gpt-4o`
3. segment-prefix match with most-specific-wins tie-break:
   `gpt-4o-mini` matches `gpt-4o` over `gpt-4` (longer last-segment prefix)

### adding pricing for a new model

if the user sees `$0.00` in their cost widget despite running queries:

```bash
# check what provider_type + model are active
grep "provider\|model" ~/.kollab/config.json | head -10

# check if that model has pricing registered
python -c "
from kollabor_ai.pricing_registry import PricingRegistry
r = PricingRegistry()
r.load_defaults()
print(r.get_pricing('custom', 'GLM-5.1'))  # replace with user's values
"
```

if it returns `None`, either:
- add the entry to `default_pricing.json` and commit (universal)
- have the user drop a `~/.kollab/pricing.json` override (local)

example user override (`~/.kollab/pricing.json`):

```json
{
  "custom": {
    "my-local-model": { "prompt_per_million": 0.0, "completion_per_million": 0.0 }
  },
  "openrouter": {
    "my/override": { "prompt_per_million": 1.00, "completion_per_million": 2.00 }
  }
}
```

the override merges into the registry on startup; no restart of the pricing
subsystem is needed — it's loaded during `_initialize_llm_core()`.

### plugin config widgets

plugins expose settings in `/config` by implementing a static method:

```python
@staticmethod
def get_config_widgets() -> dict:
    return {
        "title": "My Plugin",
        "widgets": [
            {"type": "checkbox", "label": "Enabled",
             "config_path": "plugins.my_plugin.enabled", "help": "..."},
            {"type": "slider", "label": "Timeout",
             "config_path": "plugins.my_plugin.timeout",
             "min_value": 1, "max_value": 60, "step": 1, "help": "..."},
        ],
    }
```

widget types: `checkbox`, `slider`, `dropdown`, `text_input`, `spinbox`.
auto-discovered from `plugins/` — no manual registration.

## event bus + hooks (python)

### the hook signature

```python
async def my_hook(data: dict, event) -> dict | None:
    # data is the event payload (mutable — return modified copy)
    # event is the Event object (type, timestamp, source)
    data["extra"] = "mutated"
    return data
```

**all hooks are async.** even if you don't await anything.

### registering a hook

```python
from kollabor_events.models import EventType, HookPriority

await event_bus.register_hook(
    event_type=EventType.LLM_REQUEST_PRE,
    callback=self.my_hook,
    priority=HookPriority.NORMAL.value,  # or an int
)
```

### the priorities (higher fires first)

| priority | value | use for |
|---|---|---|
| SYSTEM | 1000 | core infra, non-negotiable |
| SECURITY | 900 | permission checks, auth |
| PREPROCESSING | 500 | context injection, redaction |
| LLM | 100 | model routing, prompt tweaks |
| POSTPROCESSING | 50 | response transforms, analytics |
| DISPLAY | 10 | rendering, animations |

### event types (partial — grep `EventType` enum in `kollabor_events/models.py` for full list)

**user input:** `USER_INPUT_PRE`, `USER_INPUT`, `USER_INPUT_POST`, `KEY_PRESS_PRE/KEY_PRESS/KEY_PRESS_POST`, `PASTE_DETECTED`

**llm:** `LLM_REQUEST_PRE/REQUEST/REQUEST_POST`, `LLM_RESPONSE_PRE/RESPONSE/RESPONSE_POST`, `LLM_THINKING`, `CANCEL_REQUEST`

**tools:** `TOOL_CALL_PRE/CALL/CALL_POST`

**mcp:** `MCP_SERVER_CONNECT/CONNECTED/DISCONNECT/ERROR`, `MCP_TOOL_REGISTER/CALL_PRE/CALL_POST`

**permissions:** `PERMISSION_CHECK/GRANTED/DENIED/CONFIRMATION`

**system:** `SYSTEM_STARTUP`, `SYSTEM_READY`, `SYSTEM_SHUTDOWN`, `RENDER_FRAME`

**slash commands:** `SLASH_COMMAND_DETECTED/EXECUTE/COMPLETE/ERROR`

**sdk extension points:** `ADD_MESSAGE`, `PRE_MESSAGE_INJECT`, `POST_MESSAGE_INJECT`, `TRIGGER_LLM_CONTINUE`, `CONTEXT_SERVICE_READY`

### common gotchas

- old docs may say `PRE_API_REQUEST` — real name is `LLM_REQUEST_PRE`
- the argument shape is `(data, event)` not `(context)`
- register via `callback=`, not `handler=`

## event bus + hooks (JSON, no python needed)

`.kollab/hooks.json` — Claude Code-compatible format plus kollabor-native
event names. shell out to any command; it receives the event payload on stdin
as JSON and responds via exit code + stdout JSON.

### control contract

| signal | meaning |
|---|---|
| exit 0 | proceed (for observers, also the default) |
| exit 2 | block the action; print explanation on stderr |
| stdout JSON | structured control response (see "JSON response" below) |
| `"async": true` | fire-and-forget, don't wait; use for loggers/notifiers |

### the JSON response (for fine-grained control)

instead of plain exit codes, emit a JSON object on stdout to control the
action more precisely:

```json
{
  "decision": "approve" | "block" | "modify",
  "reason": "human-readable explanation",
  "modified_data": { /* replaces event data if decision == modify */ },
  "continue": true | false,
  "stopReason": "shown to user if continue=false",
  "suppressOutput": true | false
}
```

`approve` is equivalent to exit 0. `block` is equivalent to exit 2 with
`reason` on stderr. `modify` replaces the event payload (e.g., transform the
tool arguments before execution).

### event name support

both work:

- Claude Code names: `PreToolUse`, `PostToolUse`, `UserPromptSubmit`,
  `SessionStart`, `SessionEnd`, `Stop`, `Notification`
- kollabor native names: `LlmRequestPre`, `LlmRequestPost`, `LlmThinking`,
  `McpServerConnect`, `McpToolCallPre`, `ShellCommandPre`, `ShellCommandPost`,
  `PermissionRequest`, `CancelRequest`, `SlashCommandExecute`,
  `SlashCommandComplete`, `ModalShow`, `ModalHide`, `PasteDetected`,
  `PostToolUseFailure`

### matchers

the `matcher` field is a regex tested against tool name (for `PreToolUse`,
`PostToolUse`) or command string (for `ShellCommandPre`). examples:

- `"file_create|file_edit"` — native file tools
- `"mcp__.*"` — any MCP tool
- `"^rm\\s"` — rm commands (shell hook)
- omit `matcher` to fire on all events of that type

### working examples

**1. observer (log everything, never block)** — `async: true` so the main
pipeline doesn't wait:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python .kollab/hooks/prompt_logger.py",
            "async": true
          }
        ]
      }
    ]
  }
}
```

and `prompt_logger.py`:

```python
import json, sys
from datetime import datetime
from pathlib import Path

data = json.load(sys.stdin)
log = Path.home() / ".kollab" / "hooks.log"

text = data.get("message", "")
preview = str(text)[:80].replace("\n", " ")

with open(log, "a") as f:
    ts = datetime.now().strftime("%H:%M:%S")
    f.write(f"[{ts}] prompt: {preview}\n")
```

**2. blocker with explanation** — exit 2 blocks, stderr shows user why:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "file_create|file_create_overwrite|file_edit",
        "hooks": [
          {
            "type": "command",
            "command": "python .kollab/hooks/guard_writes.py"
          }
        ]
      }
    ]
  }
}
```

and `guard_writes.py`:

```python
import json, re, sys

data = json.load(sys.stdin)
path = (
    data.get("file", "")
    or data.get("path", "")
    or data.get("arguments", {}).get("path", "")
)

BLOCKED = [r"\.env$", r"\.env\.", r"secrets\.json", r"credentials",
           r"\.ssh/", r"\.kollab/hooks\.json"]

for pattern in BLOCKED:
    if re.search(pattern, path):
        print(f"blocked: write to {path} matches '{pattern}'", file=sys.stderr)
        sys.exit(2)
```

key payload fields: kollabor native file tools put the path at top level
(`file`, `path`); MCP tools put it under `arguments`. check all three.

**3. JSON response — block with structured reason:**

```python
import json, sys

data = json.load(sys.stdin)
tool = data.get("tool_name", "")
args = data.get("arguments", {})

if tool == "shell" and "rm -rf" in args.get("command", ""):
    print(json.dumps({
        "decision": "block",
        "reason": "rm -rf is blocked by policy; use specific paths",
    }))
    sys.exit(0)  # exit 0 — the JSON response carries the decision
```

**4. modify — transform tool arguments before execution:**

```python
import json, sys

data = json.load(sys.stdin)
args = data.get("arguments", {})

# force all file writes into a sandbox dir
if data.get("tool_name") == "file_create":
    path = args.get("path", "")
    if not path.startswith("/tmp/sandbox/"):
        args["path"] = f"/tmp/sandbox/{path.lstrip('/')}"
        print(json.dumps({
            "decision": "modify",
            "reason": f"redirected to sandbox: {args['path']}",
            "modified_data": {**data, "arguments": args},
        }))
        sys.exit(0)
```

**5. context injection — inject system context on every LLM request:**

```json
{
  "hooks": {
    "LlmRequestPre": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python .kollab/hooks/inject_context.py"
          }
        ]
      }
    ]
  }
}
```

```python
import json, subprocess, sys

data = json.load(sys.stdin)
messages = data.get("messages", [])

# gather fresh context at request time
git_branch = subprocess.check_output(
    ["git", "branch", "--show-current"], text=True
).strip()
git_status = subprocess.check_output(
    ["git", "status", "--short"], text=True
).strip()

context = f"\n[auto-context]\nbranch: {git_branch}\nstatus:\n{git_status}\n"

# prepend to the last system message, or add one
if messages and messages[0].get("role") == "system":
    messages[0]["content"] += context
else:
    messages.insert(0, {"role": "system", "content": context})

print(json.dumps({
    "decision": "modify",
    "modified_data": {**data, "messages": messages},
}))
```

**6. notifier — desktop notification on long-running completion:**

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python .kollab/hooks/notify_done.py",
            "async": true
          }
        ]
      }
    ]
  }
}
```

```python
import json, subprocess, sys

data = json.load(sys.stdin)
duration = data.get("duration_seconds", 0)

# only notify for slow responses
if duration > 30:
    msg = f"kollabor done ({duration:.0f}s)"
    subprocess.run([
        "osascript", "-e",
        f'display notification "{msg}" with title "kollabor"'
    ])
```

**7. env/time gate — only block during work hours:**

```python
import json, os, sys
from datetime import datetime

data = json.load(sys.stdin)

if os.environ.get("KOLLAB_STRICT") == "1":
    hour = datetime.now().hour
    if 9 <= hour < 17:
        tool = data.get("tool_name", "")
        if tool == "shell":
            print("shell blocked during work hours (KOLLAB_STRICT=1)",
                  file=sys.stderr)
            sys.exit(2)
```

**8. shell command audit — observe + redact secrets:**

```json
{
  "hooks": {
    "ShellCommandPre": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python .kollab/hooks/shell_audit.py",
            "async": true
          }
        ]
      }
    ]
  }
}
```

```python
import json, re, sys
from datetime import datetime
from pathlib import Path

data = json.load(sys.stdin)
cmd = data.get("command", "")

# redact obvious secrets before logging
cmd = re.sub(r"(--token[= ])\S+", r"\1***", cmd)
cmd = re.sub(r"(Authorization: Bearer )\S+", r"\1***", cmd)

log = Path.home() / ".kollab" / "shell_audit.log"
with open(log, "a") as f:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    f.write(f"[{ts}] {cmd}\n")
```

**9. multiple handlers per event — chain them:**

handlers within the same `hooks` array run sequentially. separate matchers
can be multiple entries in the event's array:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "file_create|file_edit",
        "hooks": [
          {"type": "command", "command": "python .../guard_writes.py"},
          {"type": "command", "command": "python .../format_check.py"}
        ]
      },
      {
        "matcher": "shell",
        "hooks": [
          {"type": "command", "command": "python .../shell_guard.py"}
        ]
      }
    ]
  }
}
```

### payload cheat sheet

every event's stdin includes `hook_event_name` plus event-specific fields:

- **PreToolUse / PostToolUse**: `tool_name`, `arguments`, `file`/`path` (native
  file tools), `tool_id`, and for post: `success`, `output`, `error`
- **UserPromptSubmit**: `message`, `session_id`
- **LlmRequestPre**: `model`, `messages`, `message_count`, `temperature`
- **LlmRequestPost**: `model`, `input_tokens`, `output_tokens`,
  `response_text`
- **ShellCommandPre/Post**: `command`, `cwd`, and for post: `exit_code`,
  `stdout`, `stderr`
- **PermissionRequest**: `tool_name`, `risk_level`, `reason`
- **McpToolCallPre/Post**: `server`, `tool_name`, `arguments`, `result`
- **SessionStart/End**: `session_id`, `project_path`

when unsure, dump the stdin to a file and inspect:

```python
import json, sys
from pathlib import Path
Path("/tmp/kollabor-hook-debug.jsonl").open("a").write(
    json.dumps(json.load(sys.stdin)) + "\n"
)
```

### when to pick JSON hooks vs python plugins

reach for **JSON hooks** when:
- single-file shell/python script
- linter, check, notification, logger
- language doesn't matter (can shell out to anything)
- user can copy-paste a script without understanding plugin API

reach for **python plugins** when:
- state across multiple events
- UI (message display, modal, status widget)
- cross-event coordination (e.g., measure LLM time between request and stop)
- slash command registration
- CLI arg registration
- deep integration (accessing config, renderer, llm_service directly)

## plugin architecture

### anatomy

```python
from kollabor_plugins import BasePlugin
from kollabor_events.models import EventType

class MyPlugin(BasePlugin):
    @staticmethod
    def register_cli_args(parser):
        group = parser.add_argument_group("My Plugin")
        group.add_argument("--my-flag", action="store_true")

    @staticmethod
    def handle_early_args(args) -> bool:
        # return True to exit before app starts (e.g., --capture mode)
        return False

    async def initialize(self, args=None, **kwargs):
        self.event_bus = kwargs["event_bus"]
        self.config = kwargs["config"]
        self.llm_service = kwargs.get("llm_service")
        self.renderer = kwargs.get("renderer")

    async def register_hooks(self):
        await self.event_bus.register_hook(
            event_type=EventType.LLM_REQUEST_PRE,
            callback=self.on_request,
            priority=500,
        )

    async def on_request(self, data, event):
        return data

    async def shutdown(self):
        pass

    @staticmethod
    def get_default_config() -> dict:
        return {"plugins": {"my_plugin": {"enabled": True}}}
```

### requirements

- filename must end with `_plugin.py` in `plugins/`
- class name must end with `Plugin`
- must implement `get_default_config()` (returns dict, can be empty)
- hook callbacks must be `async def`

### what gets injected in `initialize()` kwargs

`event_bus`, `config`, `command_registry`, `input_handler`, `renderer`,
`llm_service`, `conversation_logger`, `conversation_manager`.

use `kwargs.get(...)` for ones you don't need — the factory sometimes passes
subsets.

### lifecycle

1. discovery: scans `plugins/` at startup, validates filename + class shape
2. factory: instantiates with dependencies
3. `initialize()` called (await)
4. `register_hooks()` called (await)
5. events flow → hooks fire
6. `shutdown()` called on exit

## slash commands

### built-ins

`/help`, `/save`, `/profile`, `/permissions`, `/terminal`, `/hub`, `/login`,
`/mcp`, `/resume`, `/config`, `/matrix`, `/version`.

### registering a command

```python
from kollabor_events.models import CommandDefinition, CommandCategory, SubcommandInfo

command_registry.register_command(CommandDefinition(
    name="mycommand",
    description="my custom command",
    category=CommandCategory.CUSTOM,
    aliases=["mc", "mycmd"],
    handler=self.my_handler,
    subcommands=[
        SubcommandInfo("action1", "", "execute action 1"),
        SubcommandInfo("action2", "<arg>", "execute action 2"),
    ],
))
```

`handler` is an async function `(args: list[str]) -> None`. it should display
output via the message coordinator (see stdout rule below).

## the unified tool pipeline

kollabor runs **both** native tool calls (from the provider API) and XML
plugin tags (`<hub_msg>`, `<scratchpad>`, `<vault_write>`) through the same
code path. to add a new tool-like capability:

```python
# 1. tell the parser to recognize the tag
response_parser.register_plugin_tag(
    name="my_thing",
    pattern=r"<my_thing\s*([^>]*)>(.*?)</my_thing>",
    display_label="my thing",
    extractor=my_extract_fn,  # returns dict of parsed attrs + content
)

# 2. tell the executor how to run it
tool_executor.register_plugin_handler("my_thing", self.handle_my_thing)

# 3. handler returns ToolExecutionResult
async def handle_my_thing(self, tool_call):
    from kollabor_agent.models import ToolExecutionResult
    return ToolExecutionResult(
        tool_id=tool_call.id,
        tool_type="my_thing",
        success=True,
        output="result text",
    )
```

**kwargs must be `tool_id`, `tool_type`, `success`, `output`** — not
`tool_name`, `result`. older code got this wrong; new code must be correct.

this replaced 33+ regex hacks on `LLM_RESPONSE_POST` with real first-class tools.

## permission system

4 modes: `CONFIRM_ALL` (default), `DEFAULT`, `AUTO_APPROVE_EDITS`, `TRUST_ALL`.

intercepts at `TOOL_CALL_PRE` at `HookPriority.SECURITY` (900). risk assessor
pattern-matches tool names and arguments. inline prompts (no modal):

- `a` approve once, `s` session, `p` project, `d` deny, `ESC` cancel
- `A` always edits, `t` trust tool, `c` cancel (context-specific)

runtime: `/permissions show | default | strict | trust | stats | clear`.

## stdout rule (THE most important rule)

**never call `print()` or `sys.stdout.write()` in plugin code.** it corrupts
the render state (duplicate input boxes, garbled status bar, stale cursor).

### how to display messages

```python
# info / system message
renderer.message_coordinator.display_message_sequence([
    ("system", "your message", {"display_type": "info"})
])

# agent-style message (used by hub)
renderer.message_coordinator.display_message_sequence([
    ("agent", content, {"agent_color": (r, g, b), "tag_char": " > "})
])
```

### how to log (to file, not screen)

```python
import logging
logger = logging.getLogger(__name__)
logger.info("debug info here")
```

logs land at `~/.kollab/projects/<encoded>/logs/kollab.log`.

## render state rule

**never touch these on the renderer directly:**
`input_line_written`, `last_line_count`, `_last_render_content`,
`writing_messages`.

**always use `MessageDisplayCoordinator`:**

```python
# display
renderer.message_coordinator.display_message_sequence([...])

# modal / fullscreen
renderer.message_coordinator.enter_alternate_buffer()
# ... your modal code with \033[?1049h ...
renderer.message_coordinator.exit_alternate_buffer(restore_state=True)
```

direct manipulation causes duplicate input boxes and stale renders. the
coordinator pauses the render loop via `writing_messages=True`.

## dynamic system prompts (trender)

system prompts support runtime rendering via `<trender>` tags:

```markdown
<trender type="include" path="sections/00-header.md" />
<trender type="agents_list" />
<trender type="shell_aliases" />
<trender type="mcp_tools" />
<trender type="active_llm" />
<trender type="hub_identity" />
<trender type="hub_roster" />
<trender type="hub_vault" />
<trender type="hub_work_queue" />
<trender type="hub_peers" />
<trender>git log --oneline -5</trender>
```

implemented types (see `packages/kollabor-ai/src/kollabor_ai/prompt_renderer.py`):
`include`, `agents_list`, `shell_aliases`, `mcp_tools`, `active_llm`,
`hub_identity`, `hub_roster`, `hub_vault`, `hub_work_queue`, `hub_peers`,
plus raw `<trender>cmd</trender>` shell execution.

### priority order

1. CLI `--system-prompt` arg
2. `KOLLAB_SYSTEM_PROMPT` env var
3. `KOLLAB_SYSTEM_PROMPT_FILE` env var
4. local `.kollab/agents/default/system_prompt.md`
5. global `~/.kollab/agents/default/system_prompt.md`
6. built-in fallback

## agents + skills

- agents live in `bundles/agents/` (global) or `.kollab/agents/` (local, wins)
- all agents inherit from `bundles/agents/_base/` via shared sections
- skills live in `bundles/skills/<name>/SKILL.md` with frontmatter
- launch: `kollab --agent <name>` or `kollab --skill <name>`

## testing

**primary approach: JSON test specs** in `tests/tmux/specs/`.

```json
{
  "name": "feature-name",
  "config": {"command": "python main.py", "app_init_sleep": 3},
  "steps": [
    {"action": "start_app"},
    {"action": "slash_command", "command": "config"},
    {"action": "sleep", "seconds": 1},
    {"action": "capture"},
    {"action": "assert_contains", "pattern": "Settings"}
  ]
}
```

run: `tests/tmux/lib/test_runner.sh tests/tmux/specs/your-test.json`

available actions: `start_app`, `slash_command`, `type`, `send_keys`,
`capture`, `assert_contains`, `assert_not_contains`, `sleep`, `section`.

**any feature implementation is not complete until a test spec passes.**

unit tests in `tests/unit/`, run with `python -m pytest tests/unit/ -x -q`.

## startup + deployment modes

- **interactive** — full TUI, render loop, three status areas
- **pipe** — `kollab "q"` or `echo q | kollab -p`, UI suppressed, plugins still
  run (check `app.pipe_mode`)
- **daemon + attach** (phase 4.5) — `kollab --detached` forks via os.fork +
  setsid, `kollab --attach <name>` boots a TUI proxy over RPC. use
  `StateService` as the abstraction (`LocalStateService` in-process,
  `RemoteStateService` over RPC)

## hub system (peer agent mesh)

if the user is on the hub:
- agents discover via presence files in `~/.kollab/hub/presence/`
- messages route via unix sockets
- memory persists in `~/.kollab/hub/vaults/<designation>/`
  (three tiers: `stream.jsonl`, `working_memory.md`, `crystallized.md`)
- `kollab --hub msg <name> <text>` broadcasts to all, triggers only `<name>`
- to activate multiple agents, send separate `msg` to each

hub XML tags (routed through the unified tool pipeline): `<hub_msg>`,
`<hub_broadcast>`, `<hub_stop>`, `<vault_write>`, `<scratchpad>`,
`<scratchpad_append>`, `<scratchpad_get/>`, `<scratchpad_clear/>`.

## the completion checklist

before telling the user a feature/fix is done:

**robustness:**
- [ ] identified 5 failure modes and fixed them
- [ ] errors visible to user via message_coordinator (not print)
- [ ] didn't break existing functionality
- [ ] fixed pre-existing errors in nearby code if found

**testing:**
- [ ] test spec in `tests/tmux/specs/`
- [ ] unit tests in `tests/unit/` for new logic
- [ ] `ruff check` passes (0 violations)
- [ ] `py_compile` passes on modified files
- [ ] `python -m pytest tests/unit/ -x -q` green

**docs:**
- [ ] CLAUDE.md updated if new pattern/component
- [ ] README.md updated if user-facing
- [ ] docs/features/ or docs/guides/ entry created or updated

**git:**
- [ ] commit references GitHub issue (`fixes #123`)
- [ ] NO Co-Authored-By or attribution footer
- [ ] branch name: `issue-123-description`

## where to read next

- `docs/one-pager.md` — 30-second architecture overview
- `docs/architecture/` — overview, terminal-rendering, event-system
- `docs/features/` — every user-facing feature has a spec
- `docs/plugins/` — overview, development, hooks-reference
- `docs/specs/hub-message-flow.md`
- `docs/specs/unified-tool-pipeline.md`
- `CLAUDE.md` — the rules

## key files to grep

- `packages/kollabor-events/src/kollabor_events/models.py` — EventType enum
- `packages/kollabor-plugins/src/kollabor_plugins/base.py` — BasePlugin
- `kollabor/application.py` — TerminalLLMChat orchestration
- `kollabor/llm/llm_coordinator.py` — LLM pipeline
- `kollabor/config_hooks.py` — JSON hook loader
- `plugins/example_context_plugin.py` — cleanest plugin example
- `plugins/hub/plugin.py` — reference for unified tool pipeline usage
