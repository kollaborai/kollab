# Entry points, configuration and system prompts

Part of the agent guide. `CLAUDE.md` holds the rules for every change and links here;
this page holds the reference for one area. Read it before you change that area.

## Entry Points

1. **`kollab` command** (after `pip install`): Entry point `kollab = "kollabor_cli_main:cli_main"` in `pyproject.toml`
2. **`python main.py`** (development): Direct execution from local `kollabor/`

Both initialize `TerminalLLMChat` in `kollabor/application.py`.

## Configuration System

**Dot notation:** `config.get("kollabor.llm.enable_streaming", False)`

**Global directory (`~/.kollab/`):**
- `config.json` - User configuration
- `agents/` - Global agent definitions
- `projects/` - Project-specific data

**Project data (`~/.kollab/projects/<encoded-path>/`):**
- `conversations/` - JSONL logs
- `conversations/raw/` - Raw API logs
- `conversations/memory/` - Intelligence cache
- `conversations/snapshots/` - Snapshots
- `logs/` - Application logs

**Path encoding:** `/home/user/myproject` -> `home_user_myproject`

**Local directory (`.kollab/` - OPTIONAL):**
- `agents/` - Project-specific agents (override global)

**Agent resolution:** Local -> Global -> Bundled (`AgentManager.find_agent_dir`, the one resolver: the engine's `POST /sessions` uses it too, with the chat's folder as the local tier (`AgentManager(project_path=...)`); app start also seeds the bundled agents into the global dir)

**Config save targets (`/config` modal, `Ctrl+S`):**
- Local (`L`): Saves to `.kollab/config.json` in cwd (project-specific overrides)
- Global (`G`): Saves to `~/.kollab/config.json` (user-wide settings)
- Only overrides (diff from defaults) are persisted, not the full config tree
- **Reload:** `ConfigService` polls the config files' mtimes once a second on the event loop (no watchdog) and reloads, notifying callbacks, when another process saved. Its own writes refresh the stored mtimes, so a save is not reloaded twice.

### Dynamic System Prompts with `<trender>` Tags

System prompts support dynamic content rendering at runtime (`kollabor_ai.prompt_renderer`):

```markdown
<!-- File includes (most common) -->
<trender type="include" path="sections/00-header.md" />

<!-- Agents + skills listing -->
<trender type="agents_list" />

<!-- Shell aliases detected from user's shell -->
<trender type="shell_aliases" />

<!-- Connected MCP servers and tools -->
<trender type="mcp_tools" />

<!-- Active LLM profile (provider, model, endpoint, log file) -->
<trender type="active_llm" />

<!-- Hub tags (require event_bus with hub_plugin service) -->
<trender type="hub_identity" />
<trender type="hub_roster" />
<trender type="hub_vault" />
<trender type="hub_work_queue" />
<trender type="hub_peers" />

<!-- Raw shell command execution -->
<trender>git log --oneline -5</trender>
```

**Implemented types** (see `packages/kollabor-ai/src/kollabor_ai/prompt_renderer.py`):
`include`, `agents_list`, `shell_aliases`, `mcp_tools`, `active_llm`,
`hub_identity`, `hub_roster`, `hub_vault`, `hub_work_queue`, `hub_peers`,
plus raw `<trender>cmd</trender>` shell execution.

Unknown `type=` attributes are left in the prompt as-is (no error, but no render).

**Priority order:** CLI `--system-prompt` arg -> `KOLLAB_SYSTEM_PROMPT` env var -> `KOLLAB_SYSTEM_PROMPT_FILE` env var -> Local `.kollab/agents/default/system_prompt.md` -> Global `~/.kollab/agents/default/system_prompt.md` -> Bundled `bundles/agents/default/system_prompt.md` -> Built-in fallback
