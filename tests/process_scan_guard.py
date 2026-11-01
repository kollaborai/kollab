"""Policy helpers for tests that need host-wide process enumeration."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable, Mapping, Sequence
from functools import wraps
from pathlib import PurePath
from typing import Any

HOST_PROCESS_SCAN_MARKER = "host_process_enumeration"
HOST_PROCESS_SCAN_OPTION = "--allow-host-process-enumeration"
HOST_PROCESS_SCAN_SKIP_REASON = "host-wide process scans run only in the sanctioned GitHub-hosted CI/tests job"

# These exact tests exercise real ACP lifecycles or host-wide process-table scans.
# Keep them visible so collection can fail closed if a test loses its marker.
HOST_PROCESS_SCAN_TESTS = {
    "unit/test_acp_bridge.py": frozenset(
        {
            "test_startup_refusal_preserves_public_message_code_and_cleans_process",
            "test_close_after_started_worker_releases_queue_and_is_repeatable",
            "test_close_recovers_fast_detached_orphan_and_preserves_wrong_nonce_process",
            "test_startup_failure_recovers_orphan_after_root_exited_before_ownership_read",
            "test_queued_turn_after_cancel_rebases_generation",
            "test_queued_turn_keeps_first_generation_updates",
            "test_compliant_cancel_preserves_process_session_and_single_consumer",
            "test_ignored_cancel_kills_old_process_and_restarts_before_queue",
            "test_runner_uses_sdk_agent_for_streams_answer_and_closes_process",
            "test_admission_accepts_exactly_ten_waiting_turns",
            "test_prepare_cancel_holds_queue_for_delayed_owner",
        }
    ),
    "unit/test_halt_runtime_review_f2.py": frozenset(
        {"test_sweep_reaches_a_tool_child_with_no_kollab_in_its_command_line"}
    ),
    "unit/test_kill_switch_f6.py": frozenset(
        {"test_b3_the_sweep_walks_the_tree_to_a_tool_with_an_unreadable_environment"}
    ),
    "unit/test_kill_switch_f11.py": frozenset(
        {
            "test_backgrounded_jobs_of_an_exited_tool_shell_are_swept",
            "test_unrelated_groups_sessions_and_a_private_tmux_server_are_not_swept",
            "test_pid_reuse_guard",
        }
    ),
    "unit/test_kill_switch_f12.py": frozenset(
        {
            "test_forged_row_naming_a_live_victim_group_is_ignored",
            "test_a_process_named_tmux_by_argv0_is_still_swept",
        }
    ),
    "unit/test_kill_switch_f13.py": frozenset({"test_a_dead_shells_untagged_child_is_taken_through_a_tagged_parent"}),
    "unit/test_kill_switch_f14.py": frozenset(
        {
            "test_real_shell_executor_leftovers_are_swept_even_with_hidden_env",
            "test_shell_fixture_cleans_up_if_launch_or_sweep_raises",
        }
    ),
    "integration/test_acp_inbound_stdio.py": frozenset(
        {
            "test_real_stdio_mixed_initialize_and_unknown_method_have_v1_shape",
            "test_real_stdio_invalid_session_and_workspace_never_spawn_daemon",
            "test_real_owned_native_prompt_permission_tools_restore_and_eof",
            "test_real_xml_terminal_source_id_reuse_is_unique_on_wire_and_unchanged_in_history",
            "test_real_native_file_edit_diff_is_retained_after_restore",
            "test_attached_queued_cancel_and_eof_preserve_the_other_client_turn",
            "test_queued_acp_cancel_preserves_native_state_rpc_owner",
            "test_attached_native_permission_never_offers_always_and_eof_preserves_owner",
            "test_active_native_cancel_stops_owned_tool_children",
            "test_real_stdio_keeps_python_print_and_fd_write_off_protocol",
            "test_real_sdk_harness_opaque_permission_diff_cards_and_restore",
            "test_real_sdk_active_cancel_partial_history_survives_close_and_reopen",
            "test_real_sdk_attached_harness_permission_filters_always_and_detaches",
            "test_real_sdk_harness_denied_or_nonoffered_choice_never_writes",
            "test_real_stdio_startup_error_has_guidance_and_no_owned_daemon",
            "test_real_sdk_harness_expired_login_fails_prompt_without_output",
        }
    ),
    "integration/test_acp_parent_drop.py": frozenset(
        {"test_owner_drop_retires_daemon_and_sdk_without_affecting_detached_agent"}
    ),
    "integration/test_hub_agent_survives_engine_death.py": frozenset({"test_agent_survives_spawner_death"}),
}
EXPECTED_CI_ENVIRONMENT = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_WORKFLOW": "CI",
    "GITHUB_JOB": "tests",
    "GITHUB_REPOSITORY": "kollaborai/kollab",
    "RUNNER_OS": "Linux",
    "RUNNER_ENVIRONMENT": "github-hosted",
}
ALLOWED_GITHUB_EVENTS = frozenset({"pull_request", "push", "workflow_dispatch"})
EXPECTED_WORKFLOW_REF_PREFIX = "kollaborai/kollab/.github/workflows/ci.yml@"


def host_process_scan_environment_errors(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Return missing/mismatched CI metadata names without echoing their values."""
    errors = [name for name, expected in EXPECTED_CI_ENVIRONMENT.items() if environ.get(name) != expected]
    if environ.get("GITHUB_EVENT_NAME") not in ALLOWED_GITHUB_EVENTS:
        errors.append("GITHUB_EVENT_NAME")
    if not environ.get("GITHUB_WORKFLOW_REF", "").startswith(EXPECTED_WORKFLOW_REF_PREFIX):
        errors.append("GITHUB_WORKFLOW_REF")
    return tuple(errors)


def host_process_scans_allowed(option_enabled: bool, environ: Mapping[str, str]) -> bool:
    """Require both the explicit pytest option and the approved CI job metadata."""
    return option_enabled and not host_process_scan_environment_errors(environ)


def _command_tokens(command: Any) -> list[str]:
    if isinstance(command, str):
        try:
            return shlex.split(command)
        except ValueError:
            return []
    if isinstance(command, Sequence):
        return [os.fspath(part) if isinstance(part, os.PathLike) else str(part) for part in command]
    return []


def is_process_table_enumeration_command(command: Any) -> bool:
    """Block broad ``ps`` commands; allow only explicit PID-targeted queries."""
    tokens = _command_tokens(command)
    for index, token in enumerate(tokens):
        name = PurePath(token).name
        if name in {"sh", "bash", "zsh"}:
            shell_args = tokens[index + 1 :]
            for shell_index, option in enumerate(shell_args[:-1]):
                command_option = option in {"-c", "--command"} or (
                    option.startswith("-") and not option.startswith("--") and "c" in option[1:]
                )
                if command_option and is_process_table_enumeration_command(shell_args[shell_index + 1]):
                    return True
        if name != "ps":
            continue

        options = tokens[index + 1 :]
        for option in options:
            if option in {
                "-A",
                "-e",
                "--all",
                "-U",
                "-u",
                "ax",
                "aux",
                "-ax",
                "-aux",
                "-ef",
                "-eF",
            }:
                return True
            if option.startswith(("-U", "-u")) and len(option) > 2:
                return True
            if option.startswith("-") and len(option) > 2:
                flags = option[1:]
                if "A" in flags or "e" in flags or {"a", "x"} <= set(flags):
                    return True

        has_pid_selector = any(
            option in {"-p", "--pid"}
            or option.startswith("--pid=")
            or (option.startswith("-p") and option[2:].isdigit())
            for option in options
        )
        if not has_pid_selector:
            return True
    return False


def guarded_process_enumeration(operation: str, original: Callable[..., Any], *, allowed: bool) -> Callable[..., Any]:
    """Return the original call only when authorized; otherwise fail before it runs."""
    if allowed:
        return original

    @wraps(original)
    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(
            f"host process enumeration blocked for unmarked test operation: {operation}; "
            f"use @{HOST_PROCESS_SCAN_MARKER} only in the sanctioned CI/tests job"
        )

    return blocked
