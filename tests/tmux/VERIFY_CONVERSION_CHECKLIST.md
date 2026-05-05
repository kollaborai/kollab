# Verify Script to JSON Test Conversion Checklist

## Instructions for Parallel Agent Work

### Agent Workflow:
1. Find a test with status `TODO` in the table below
2. Edit this file: change status to `IN_PROGRESS` and add your agent name
3. Read the bash script in `tests/tmux/`
4. Create equivalent JSON spec in `tests/tmux/specs/`
5. Test it: `./tests/tmux/lib/test_runner.sh tests/tmux/specs/your-test.json`
6. Update this file: change status to `DONE` when complete
7. Select another `TODO` test and repeat

### Agent Names:
- Use: `agent1`, `agent2`, `agent3`

### Conversion Guidelines:
- JSON filename: same as bash but `.json` instead of `.sh`
- Include `show_captures: false` in config (unless debugging)
- Use `assert_contains` with flexible patterns (e.g., `"pattern|text|alternative"`)
- Add `sleep` after actions that trigger animations
- Test both success and error cases

---

## Conversion Status

| Status | Agent | Bash Script | JSON Target | Priority | Notes |
|--------|-------|-------------|-------------|----------|-------|
| DONE | agent2 | verify_add_widget_to_hidden_row.sh | verify_add_widget_to_hidden_row.json | MED | Widget to hidden row |
| DONE | agent3 | verify_color_toggle.sh | verify_color_toggle.json | MED | Color toggle |
| DONE | agent1 | verify_delete_widget.sh | verify_delete_widget.json | HIGH | Delete widget |
| DONE | agent3 | verify_delete_widget_final.sh | verify_delete_widget_final.json | MED | Delete widget final |
| DONE | agent3 | verify_delete_widget_row_index_fix.sh | verify_delete_widget_row_index_fix.json | MED | Delete widget row fix |
| DONE | agent2 | verify_delete_widget_v2.sh | verify_delete_widget_v2.json | MED | Delete widget v2 |
| DONE | agent2 | verify_edit_mode.sh | verify_edit_mode.json | HIGH | Edit mode |
| DONE | agent3 | verify_edit_mode_exit.sh | verify_edit_mode_exit.json | HIGH | Edit mode exit |
| DONE | agent3 | verify_first_run_help.sh | verify_first_run_help.json | MED | First run help |
| DONE | agent2 | verify_help_overlay.sh | verify_help_overlay.json | HIGH | Help overlay |
| DONE | agent2 | verify_inline_editor_connection.sh | verify_inline_editor_connection.json | HIGH | Inline editor |
| DONE | agent2 | verify_inline_editor_fix.sh | verify_inline_editor_fix.json | MED | Inline editor fix |
| DONE | agent1 | verify_inline_editor_label_widget.sh | verify_inline_editor_label_widget.json | MED | Inline editor label |
| DONE | agent3 | verify_inline_slider.sh | verify_inline_slider.json | MED | Inline slider |
| DONE | agent3 | verify_inline_text_input.sh | verify_inline_text_input.json | MED | Inline text input |
| DONE | agent2 | verify_input_box_hiding.sh | verify_input_box_hiding.json | HIGH | Input box hiding |
| DONE | agent2 | verify_input_hiding.sh | verify_input_hiding.json | HIGH | Input hiding |
| DONE | agent2 | verify_label_edit.sh | verify_label_edit.json | MED | Label edit |
| DONE | agent2 | verify_label_edit_reactivation.sh | verify_label_edit_reactivation.json | MED | Label edit reactivation |
| DONE | agent2 | verify_label_widget_inline_edit.sh | verify_label_widget_inline_edit.json | MED | Label widget inline |
| DONE | agent3 | verify_mcp_integration.sh | verify_mcp_integration.json | HIGH | MCP integration |
| DONE | agent3 | verify_mcp_local_server_connection.sh | verify_mcp_local_server_connection.json | HIGH | MCP server connection |
| DONE | agent1 | verify_navigation_mode.sh | verify_navigation_mode.json | HIGH | Navigation mode |
| DONE | agent2 | verify_parallel_spawn.sh | verify_parallel_spawn.json | MED | Parallel spawn |
| DONE | agent2 | verify_persistence.sh | verify_persistence.json | MED | Persistence |
| DONE | agent1 | verify_quick_jump.sh | verify_quick_jump.json | MED | Quick jump |
| DONE | agent3 | verify_quick_jump_edit_mode.sh | verify_quick_jump_edit_mode.json | MED | Quick jump edit mode |
| DONE | agent2 | verify_script_refresh.sh | verify_script_refresh.json | LOW | Script refresh |
| DONE | agent2 | verify_script_widget_registration.sh | verify_script_widget_registration.json | LOW | Script widget reg |
| DONE | agent1 | verify_slash_commands.sh | verify_slash_commands.json | HIGH | Slash commands |
| DONE | agent2 | verify_slot_navigation.sh | verify_slot_navigation.json | HIGH | Slot navigation |
| DONE | agent2 | verify_state_cleanup_after_edit.sh | verify_state_cleanup_after_edit.json | MED | State cleanup |
| DONE | agent1 | verify_toggle_persistence.sh | verify_toggle_persistence.json | MED | Toggle persistence |
| DONE | agent2 | verify_toggle_widgets.sh | verify_toggle_widgets.json | MED | Toggle widgets |
| DONE | agent1 | verify_undo.sh | verify_undo.json | LOW | Undo |
| DONE | agent1 | verify_widget_picker.sh | verify_widget_picker.json | HIGH | Widget picker |

---

## Priority Guide

- **HIGH**: Core functionality, user workflows, critical UI features (12 tests)
- **MED**: Feature testing, edge cases (21 tests)
- **LOW**: Minor features, unimplemented (3 tests)
