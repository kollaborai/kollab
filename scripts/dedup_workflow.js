export const meta = {
  name: 'dedup-scour',
  description: 'Triage mechanically-detected duplicate-function clusters, classify each safe-vs-report-only',
  phases: [
    { title: 'Triage' },
    { title: 'Synthesize' },
  ],
}

const CLUSTERS = [{"id":0,"count":2,"cross_file":true,"loc":35,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/message_renderer.py","line":195,"name":"_tool_badge","loc":35},{"file":"packages/kollabor-tui/src/kollabor_tui/clean_renderer.py","line":251,"name":"_tool_badge","loc":35}]},{"id":1,"count":2,"cross_file":true,"loc":19,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/message_renderer.py","line":150,"name":"_clean_tool_detail","loc":19},{"file":"packages/kollabor-tui/src/kollabor_tui/clean_renderer.py","line":215,"name":"_clean_tool_detail","loc":19}]},{"id":2,"count":2,"cross_file":true,"loc":13,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/message_renderer.py","line":171,"name":"_tool_symbol","loc":13},{"file":"packages/kollabor-tui/src/kollabor_tui/clean_renderer.py","line":236,"name":"_tool_symbol","loc":13}]},{"id":3,"count":2,"cross_file":true,"loc":10,"members":[{"file":"packages/kollabor-config/src/kollabor_config/loader.py","line":40,"name":"detect_provider_from_api_key","loc":10},{"file":"packages/kollabor-ai/src/kollabor_ai/profile_validator.py","line":48,"name":"detect_provider_from_api_key","loc":10}]},{"id":4,"count":2,"cross_file":true,"loc":10,"members":[{"file":"plugins/altview/conversations_altview.py","line":359,"name":"_render_footer","loc":10},{"file":"plugins/fullscreen/conversations_plugin.py","line":375,"name":"_render_footer","loc":10}]},{"id":5,"count":2,"cross_file":true,"loc":10,"members":[{"file":"plugins/altview/conversations_altview.py","line":384,"name":"_get_display_name","loc":10},{"file":"plugins/fullscreen/conversations_plugin.py","line":396,"name":"_get_display_name","loc":10}]},{"id":6,"count":2,"cross_file":true,"loc":9,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/message_renderer.py","line":61,"name":"_tool_summary_color","loc":9},{"file":"packages/kollabor-tui/src/kollabor_tui/clean_renderer.py","line":126,"name":"_tool_summary_color","loc":9}]},{"id":7,"count":2,"cross_file":true,"loc":9,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/message_renderer.py","line":137,"name":"_named_arg","loc":9},{"file":"packages/kollabor-tui/src/kollabor_tui/clean_renderer.py","line":202,"name":"_named_arg","loc":9}]},{"id":8,"count":2,"cross_file":true,"loc":8,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/message_renderer.py","line":258,"name":"_format_tool_summary","loc":8},{"file":"packages/kollabor-tui/src/kollabor_tui/clean_renderer.py","line":314,"name":"_format_tool_summary","loc":8}]},{"id":9,"count":3,"cross_file":true,"loc":7,"members":[{"file":"kollabor/commands/system_commands/handlers/skills.py","line":60,"name":"llm_service","loc":7},{"file":"kollabor/commands/system_commands/handlers/profile.py","line":64,"name":"llm_service","loc":7},{"file":"kollabor/commands/system_commands/handlers/agent.py","line":63,"name":"llm_service","loc":7}]},{"id":10,"count":3,"cross_file":true,"loc":7,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/message_renderer.py","line":186,"name":"_truncate_plain","loc":7},{"file":"packages/kollabor-tui/src/kollabor_tui/message_renderer.py","line":559,"name":"_truncate_plain","loc":7},{"file":"packages/kollabor-tui/src/kollabor_tui/clean_renderer.py","line":100,"name":"_truncate_plain","loc":7}]},{"id":11,"count":2,"cross_file":true,"loc":7,"members":[{"file":"kollabor/cli.py","line":1288,"name":"_pid_alive","loc":7},{"file":"plugins/hub/plugin.py","line":7624,"name":"_agent_pid_alive","loc":8}]},{"id":12,"count":2,"cross_file":true,"loc":6,"members":[{"file":"packages/kollabor-ai/src/kollabor_ai/adapters/openai_adapter.py","line":317,"name":"get_headers","loc":6},{"file":"packages/kollabor-ai/src/kollabor_ai/adapters/base.py","line":209,"name":"get_headers","loc":6}]},{"id":13,"count":12,"cross_file":true,"loc":5,"members":[{"file":"kollabor/attach_client.py","line":42,"name":"_get_loop","loc":5},{"file":"packages/kollabor-agent/src/kollabor_agent/process_manager.py","line":13,"name":"_get_loop","loc":5},{"file":"packages/kollabor-agent/src/kollabor_agent/mcp_integration.py","line":42,"name":"_get_loop","loc":5},{"file":"packages/kollabor-tui/src/kollabor_tui/render_loop.py","line":30,"name":"_get_loop","loc":5},{"file":"packages/kollabor-tui/src/kollabor_tui/fullscreen/session.py","line":6,"name":"_get_loop","loc":5},{"file":"packages/kollabor-tui/src/kollabor_tui/fullscreen/plugin.py","line":6,"name":"_get_loop","loc":5},{"file":"packages/kollabor-tui/src/kollabor_tui/fullscreen/manager.py","line":6,"name":"_get_loop","loc":5},{"file":"plugins/altview/matrix_altview.py","line":14,"name":"_get_loop","loc":5},{"file":"plugins/fullscreen/matrix_plugin.py","line":6,"name":"_get_loop","loc":5},{"file":"plugins/fullscreen/example_plugin.py","line":21,"name":"_get_loop","loc":5},{"file":"plugins/fullscreen/space_shooter_plugin.py","line":10,"name":"_get_loop","loc":5},{"file":"plugins/hub/plugin.py","line":89,"name":"_get_loop","loc":5}]},{"id":14,"count":2,"cross_file":true,"loc":5,"members":[{"file":"kollabor/commands/system_commands/handlers/model.py","line":50,"name":"llm_service","loc":5},{"file":"kollabor/commands/system_commands/handlers/login.py","line":43,"name":"llm_service","loc":4}]},{"id":15,"count":2,"cross_file":true,"loc":5,"members":[{"file":"packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py","line":1250,"name":"_get_context_service","loc":5},{"file":"plugins/hub/plugin.py","line":1915,"name":"_get_context_service","loc":5}]},{"id":16,"count":2,"cross_file":true,"loc":5,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/message_renderer.py","line":249,"name":"_render_tool_badge","loc":5},{"file":"packages/kollabor-tui/src/kollabor_tui/clean_renderer.py","line":305,"name":"_render_tool_badge","loc":5}]},{"id":17,"count":2,"cross_file":true,"loc":5,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/message_renderer.py","line":310,"name":"_agent_marker","loc":5},{"file":"packages/kollabor-tui/src/kollabor_tui/clean_renderer.py","line":392,"name":"_agent_marker","loc":5}]},{"id":18,"count":2,"cross_file":true,"loc":5,"members":[{"file":"plugins/terminal_plugin.py","line":75,"name":"is_alive","loc":5},{"file":"plugins/agent_orchestrator/models.py","line":44,"name":"is_alive","loc":5}]},{"id":19,"count":2,"cross_file":true,"loc":5,"members":[{"file":"plugins/altview/conversations_altview.py","line":55,"name":"set_app","loc":5},{"file":"plugins/fullscreen/conversations_plugin.py","line":63,"name":"set_app","loc":5}]},{"id":20,"count":2,"cross_file":true,"loc":5,"members":[{"file":"plugins/altview/conversations_altview.py","line":484,"name":"get_resume_session","loc":5},{"file":"plugins/fullscreen/conversations_plugin.py","line":503,"name":"get_resume_session","loc":5}]},{"id":21,"count":2,"cross_file":true,"loc":5,"members":[{"file":"plugins/fullscreen/matrix_plugin.py","line":114,"name":"handle_input","loc":5},{"file":"plugins/fullscreen/space_shooter_plugin.py","line":120,"name":"handle_input","loc":5}]},{"id":22,"count":3,"cross_file":true,"loc":4,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/design_system/inline_widgets.py","line":29,"name":"_fg","loc":4},{"file":"packages/kollabor-tui/src/kollabor_tui/status/utils.py","line":10,"name":"fg","loc":4},{"file":"plugins/terminal_plugin.py","line":554,"name":"_fg","loc":3}]},{"id":23,"count":2,"cross_file":true,"loc":4,"members":[{"file":"packages/kollabor-ai/src/kollabor_ai/response_parser.py","line":892,"name":"_extract_thinking","loc":4},{"file":"packages/kollabor-ai/src/kollabor_ai/response_processor.py","line":93,"name":"_extract_thinking","loc":4}]},{"id":24,"count":2,"cross_file":true,"loc":4,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/message_renderer.py","line":44,"name":"_mix_color","loc":4},{"file":"packages/kollabor-tui/src/kollabor_tui/clean_renderer.py","line":109,"name":"_mix_color","loc":4}]},{"id":25,"count":2,"cross_file":true,"loc":4,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/message_renderer.py","line":72,"name":"_normalize_tool_label","loc":4},{"file":"packages/kollabor-tui/src/kollabor_tui/clean_renderer.py","line":137,"name":"_normalize_tool_label","loc":4}]},{"id":26,"count":2,"cross_file":true,"loc":4,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/render_layout.py","line":365,"name":"_request_render","loc":4},{"file":"packages/kollabor-tui/src/kollabor_tui/altview/base.py","line":111,"name":"request_render","loc":4}]},{"id":27,"count":3,"cross_file":true,"loc":3,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/render_layout.py","line":86,"name":"_strip_ansi","loc":3},{"file":"packages/kollabor-tui/src/kollabor_tui/modals/modal_renderer.py","line":1167,"name":"_strip_ansi","loc":3},{"file":"packages/kollabor-tui/src/kollabor_tui/modals/modal_state_manager.py","line":82,"name":"_strip_ansi","loc":3}]},{"id":28,"count":3,"cross_file":true,"loc":3,"members":[{"file":"plugins/fullscreen/matrix_plugin.py","line":130,"name":"on_stop","loc":3},{"file":"plugins/fullscreen/example_plugin.py","line":1488,"name":"on_stop","loc":3},{"file":"plugins/fullscreen/space_shooter_plugin.py","line":136,"name":"on_stop","loc":3}]},{"id":29,"count":2,"cross_file":true,"loc":3,"members":[{"file":"kollabor/updates/auto_update.py","line":33,"name":"_current_package_root","loc":3},{"file":"kollabor/updates/git_update.py","line":44,"name":"_current_source_root","loc":3}]},{"id":30,"count":2,"cross_file":true,"loc":3,"members":[{"file":"packages/kollabor-config/src/kollabor_config/plugin_config_manager.py","line":14,"name":"_has_method","loc":3},{"file":"packages/kollabor-plugins/src/kollabor_plugins/plugin_utils.py","line":10,"name":"has_method","loc":3}]},{"id":31,"count":2,"cross_file":true,"loc":3,"members":[{"file":"packages/kollabor-engine/src/kollabor_engine/hub_bridge.py","line":30,"name":"_encode_project_path","loc":3},{"file":"plugins/hub/project_scope.py","line":51,"name":"_encode_path","loc":3}]},{"id":32,"count":2,"cross_file":true,"loc":3,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/message_renderer.py","line":56,"name":"_assistant_text_color","loc":3},{"file":"packages/kollabor-tui/src/kollabor_tui/clean_renderer.py","line":121,"name":"_assistant_text_color","loc":3}]},{"id":33,"count":2,"cross_file":true,"loc":3,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/render_layout.py","line":361,"name":"set_render_loop","loc":3},{"file":"packages/kollabor-tui/src/kollabor_tui/input/display_controller.py","line":58,"name":"set_render_loop","loc":3}]},{"id":34,"count":2,"cross_file":true,"loc":3,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/input/display_controller.py","line":66,"name":"set_event_bus","loc":3},{"file":"plugins/altview/hub_console_altview.py","line":75,"name":"set_event_bus","loc":3}]},{"id":35,"count":2,"cross_file":true,"loc":3,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/status/navigation_state.py","line":160,"name":"is_active","loc":3},{"file":"packages/kollabor-tui/src/kollabor_tui/fullscreen/renderer.py","line":329,"name":"is_active","loc":3}]},{"id":36,"count":2,"cross_file":true,"loc":3,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/status/widget_picker.py","line":19,"name":"_strip_ansi","loc":3},{"file":"plugins/altview/widget_picker_altview.py","line":19,"name":"_strip_ansi","loc":3}]},{"id":37,"count":2,"cross_file":true,"loc":3,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/status/widget_picker.py","line":97,"name":"get_selected_widget","loc":3},{"file":"plugins/altview/widget_picker_altview.py","line":119,"name":"selected_widget_id","loc":3}]},{"id":38,"count":2,"cross_file":false,"loc":18,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/status/modal_presenter.py","line":179,"name":"read_key","loc":18},{"file":"packages/kollabor-tui/src/kollabor_tui/status/modal_presenter.py","line":527,"name":"read_key","loc":18}]},{"id":39,"count":2,"cross_file":false,"loc":5,"members":[{"file":"packages/kollabor-ai/src/kollabor_ai/providers/security.py","line":306,"name":"get_key","loc":5},{"file":"packages/kollabor-ai/src/kollabor_ai/providers/security.py","line":514,"name":"get_key","loc":5}]},{"id":40,"count":2,"cross_file":false,"loc":5,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/input_handler.py","line":235,"name":"command_mode","loc":5},{"file":"packages/kollabor-tui/src/kollabor_tui/input_handler.py","line":241,"name":"_sync_command_mode","loc":5}]},{"id":41,"count":2,"cross_file":false,"loc":4,"members":[{"file":"packages/kollabor-engine/src/kollabor_engine/server.py","line":126,"name":"safe_ver","loc":4},{"file":"packages/kollabor-engine/src/kollabor_engine/server.py","line":146,"name":"safe_ver","loc":4}]},{"id":42,"count":2,"cross_file":false,"loc":4,"members":[{"file":"plugins/hub/crystal_store.py","line":207,"name":"load","loc":4},{"file":"plugins/hub/crystal_store.py","line":350,"name":"get_all","loc":4}]},{"id":43,"count":2,"cross_file":false,"loc":4,"members":[{"file":"plugins/hub/presence.py","line":95,"name":"publish","loc":4},{"file":"plugins/hub/presence.py","line":100,"name":"heartbeat","loc":4}]},{"id":44,"count":2,"cross_file":false,"loc":4,"members":[{"file":"plugins/hub/plugin.py","line":1290,"name":"_extract_vault_write","loc":4},{"file":"plugins/hub/plugin.py","line":1320,"name":"_extract_global_vault_write","loc":4}]},{"id":45,"count":2,"cross_file":false,"loc":3,"members":[{"file":"packages/kollabor-tui/src/kollabor_tui/status/toggle_handler.py","line":197,"name":"can_cycle_next","loc":3},{"file":"packages/kollabor-tui/src/kollabor_tui/status/toggle_handler.py","line":205,"name":"can_cycle_prev","loc":3}]}]

const clusters = Array.isArray(args) && args.length ? args : CLUSTERS

const TRIAGE_SCHEMA = {
  type: 'object',
  required: ['id', 'verdict', 'category', 'safe_to_autofix', 'rationale', 'canonical', 'callers_note'],
  properties: {
    id: { type: 'integer', description: 'cluster id, echo back unchanged' },
    verdict: {
      type: 'string',
      enum: ['real_dupe', 'not_dupe', 'intentional'],
      description:
        'real_dupe = same logic copy-pasted, should be consolidated. not_dupe = coincidentally identical body but different semantic intent / different module contract, leave alone. intentional = same-file sibling or framework override that is supposed to exist.',
    },
    category: {
      type: 'string',
      enum: ['safe_util', 'render_path', 'hub_flow', 'cross_package_contract', 'risky_other', 'na'],
      description:
        'safe_util = pure utility (string/path/color/pid/ansi/asyncio-loop helper), no UI side effects, safe to consolidate. render_path = lives in kollabor-tui message_coordinator/renderer/clean_renderer/render_loop hot path -> REPORT ONLY. hub_flow = hub message/spawn/continuation logic in plugins/hub -> REPORT ONLY. cross_package_contract = duplicated across packages where merging creates a new cross-package dependency -> human call. risky_other = real dupe but consolidation non-trivial. na = for not_dupe/intentional.',
    },
    safe_to_autofix: {
      type: 'boolean',
      description:
        'true ONLY if verdict=real_dupe AND category=safe_util AND no copy is in the tui render hot path or hub flow AND consolidation is mechanical. When unsure, false.',
    },
    canonical: {
      type: 'string',
      description:
        'if real_dupe: which file:line is the single source of truth, or propose a new shared module path. empty string otherwise.',
    },
    callers_note: {
      type: 'string',
      description:
        'how many call sites reference each copy and where (grep the name). decides mechanical-vs-entangled.',
    },
    rationale: { type: 'string', description: '1-3 sentences: why this verdict + category.' },
  },
}

phase('Triage')

const triaged = await pipeline(
  clusters,
  (cluster) =>
    agent(
      `You are auditing a candidate DUPLICATE-FUNCTION cluster in the Kollab codebase (cwd is the repo root).

A mechanical AST pass found these functions share a byte-identical normalized body (docstrings/whitespace stripped, variable names ignored):

${JSON.stringify(cluster, null, 2)}

YOUR JOB: open the ACTUAL files at the given line numbers (Read each member), then decide:

1. verdict:
   - real_dupe = genuine copy-paste, should be consolidated into one home.
   - not_dupe = bodies happen to match but the functions mean different things in their modules (e.g. a 3-line "return self._result" getter in two unrelated classes is a COINCIDENCE, not a dupe to merge). Leave alone.
   - intentional = same-file sibling / framework override that is SUPPOSED to exist (e.g. a setter + its _sync_ variant, two nested closures in one function, an alias method like publish/heartbeat, next/prev predicate pair, an "async def f(self): await super().f()" framework hook). Lean here for same-file pairs (same path, two line numbers).

2. category (per schema). HARD CONSTRAINTS from the project CLAUDE.md:
   - kollabor-tui RENDER HOT PATH (message_coordinator.py, terminal_renderer.py, message_renderer.py, clean_renderer.py, render_loop.py, render_layout.py) = REPORT ONLY -> render_path, safe_to_autofix=false. Behavioral changes there need live verification an auto-fixer cannot do.
   - hub message flow / spawn / LLM continuation (plugins/hub/plugin.py routing) = REPORT ONLY -> hub_flow, safe_to_autofix=false.
   - pure utilities (ansi strip, color mix, pid liveness, asyncio loop getter, header builder, path encode, hasattr check) with no UI side effects = safe_util.

3. safe_to_autofix: true ONLY when real_dupe AND safe_util AND not render/hub AND mechanical merge. Grep the function name to count call sites and confirm repointing is straightforward. When in doubt -> false.

Fill 'canonical' (file:line or new shared-module path that should own the function) and 'callers_note' (grep result: how many callers per copy, where).

Echo the cluster id (${cluster.id}) back unchanged. Return ONLY the structured object.`,
      { label: `triage:${cluster.id}:${cluster.members[0].name}`, phase: 'Triage', schema: TRIAGE_SCHEMA }
    ),
)

const valid = triaged.filter(Boolean)

phase('Synthesize')
log(`triaged ${valid.length}/${clusters.length} clusters`)

const safe = valid.filter((v) => v.safe_to_autofix)
const realDupes = valid.filter((v) => v.verdict === 'real_dupe')

return {
  total: clusters.length,
  triaged: valid.length,
  real_dupes: realDupes.length,
  safe_autofix: safe.length,
  results: valid,
  clusters,
}
