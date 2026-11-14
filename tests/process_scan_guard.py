"""Policy helpers for tests that need host-wide process enumeration.

This is a guardrail against accidental scans through common public APIs, not a
sandbox. References captured before the fixture, raw procfs/proc interfaces,
native APIs, private psutil platform helpers, and separately launched child
code can bypass it; tests needing those are outside this guard's coverage and
must not treat it as an isolation boundary.
"""

from __future__ import annotations

import base64
import os
import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from functools import wraps
from pathlib import PurePath
from typing import Any

HOST_PROCESS_SCAN_MARKER = "host_process_enumeration"
HOST_PROCESS_SCAN_OPTION = "--allow-host-process-enumeration"
HOST_PROCESS_SCAN_SKIP_REASON = "host-wide process scans run only in a sanctioned GitHub-hosted CI test job"

# These exact tests exercise real ACP lifecycles or host-wide process-table scans.
# Keep them visible so collection can fail closed if a test loses its marker.
HOST_PROCESS_SCAN_TESTS = {
    "unit/test_acp_bridge.py": frozenset(
        {
            "test_close_recovers_fast_detached_orphan_and_preserves_other_session_process",
            "test_setsid_orphan_lost_before_a_tree_sample_is_the_documented_limit",
            "test_private_process_group_cleans_orphan_without_session_lookup",
            "test_orphan_fixture_defers_cleanup_when_runner_close_raises",
            "test_startup_orphan_fixture_defers_when_product_cleanup_is_broken",
            "test_startup_failure_cleans_same_group_orphan_after_root_exits",
        }
    ),
    "unit/test_acp_orchestrator.py": frozenset(
        {
            "test_real_sdk_interrupted_turn_survives_snapshot_and_reload_without_replacement_updates",
            "test_history_error_after_assistant_write_does_not_retry_or_duplicate_terminal",
            "test_failed_user_row_write_keeps_the_finished_reply",
            "test_failed_user_row_write_is_shown_not_swallowed",
            "test_failed_reply_row_write_keeps_the_finished_reply",
        }
    ),
    "unit/test_acp_cancel_transcript.py": frozenset(
        {
            "test_cancelled_turn_is_stored_once_with_the_reply_streamed_before_the_cancel",
            "test_prose_the_agent_sends_after_the_cancel_is_neither_stored_nor_streamed",
        }
    ),
    "unit/test_acp_managed_resolution.py": frozenset(
        {
            "test_real_resolver_launches_selected_adapter_and_projects_its_usage",
            "test_managed_upgrade_resumes_saved_session_without_changing_other_bindings",
            "test_managed_upgrade_resume_failure_does_not_overwrite_binding",
            "test_managed_upgrade_without_resume_capability_preserves_binding",
            "test_managed_upgrade_cancel_does_not_overwrite_binding",
            "test_unrecognized_global_keeps_old_binding_but_still_runs_for_new_sessions",
        }
    ),
    "unit/test_acp_npm_shims.py": frozenset({"test_generated_shim_old_binding_resume_or_preservation"}),
    "unit/test_cli_web_ui_engine_port.py": frozenset(
        {
            "test_an_engine_on_this_interpreter_is_ours",
            "test_an_engine_on_another_interpreter_is_not",
            "test_a_port_with_no_engine_is_not_ours",
        }
    ),
    "unit/test_cli_web_ui_reuse.py": frozenset(
        {
            "test_restart_stops_the_launcher_that_owns_the_ui",
            "test_restart_stops_a_ui_started_by_hand",
        }
    ),
    "unit/test_halt_confirmed.py": frozenset({"test_the_daemon_refuses_an_unconfirmed_act"}),
    "unit/test_hub_enrollment_commands.py": frozenset({"test_revoke_drops_the_peer_device_binding"}),
    "unit/test_hub_network_surface.py": frozenset({"test_connect_revoke_rejects_an_unknown_device"}),
    "unit/test_network_halt.py": frozenset({"test_read_process_reads_this_process"}),
    "unit/test_network_halt_windows.py": frozenset({"test_win_processes_links_this_process_to_its_parent"}),
    "unit/test_stranger_delivery.py": frozenset({"test_revoke_removes_the_link_at_the_relay_and_nothing_is_delivered"}),
    "unit/test_halt_runtime_review_f2.py": frozenset(
        {"test_sweep_reaches_a_tool_child_with_no_kollab_in_its_command_line"}
    ),
    "unit/test_kill_switch_f6.py": frozenset(
        {"test_b3_the_sweep_walks_the_tree_to_a_tool_with_an_unreadable_environment"}
    ),
    "unit/test_kill_switch_f10.py": frozenset(
        {
            "test_new_1_hub_path_refuses_roster_and_count_acts_by_the_callers_chain",
            "test_new_1_nc_with_an_empty_env_under_an_agent_cannot_lift_a_halt",
        }
    ),
    "unit/test_kill_switch_f11.py": frozenset(
        {
            "test_backgrounded_jobs_of_an_exited_tool_shell_are_swept",
            "test_unrelated_groups_sessions_and_a_private_tmux_server_are_not_swept",
            "test_pid_reuse_guard",
            "test_new_3_a_caller_chain_that_cannot_be_read_is_refused",
            "test_new_3_an_unreadable_process_of_this_user_in_the_chain_is_refused",
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
            "test_sweep_survives_a_ps_start_drift",
        }
    ),
    "integration/test_acp_conversation_ownership.py": frozenset(
        {"test_real_sdk_child_retries_clear_and_resume_without_old_context"}
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
    "integration/test_acp_startup_loadouts.py": frozenset({"test_real_acp_child_confirms_or_cleans_up_startup"}),
    "integration/test_acp_startup_ownership.py": frozenset(
        {"test_real_acp_child_overlap_refuses_rpc_then_successor_handles_turn"}
    ),
    "integration/test_acp_parent_drop.py": frozenset(
        {"test_owner_drop_retires_daemon_and_sdk_without_affecting_detached_agent"}
    ),
    "integration/test_hub_agent_survives_engine_death.py": frozenset({"test_agent_survives_spawner_death"}),
}
EXPECTED_CI_ENVIRONMENT = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_WORKFLOW": "CI",
    "GITHUB_REPOSITORY": "kollaborai/kollab",
    "RUNNER_OS": "Linux",
    "RUNNER_ENVIRONMENT": "github-hosted",
}
ALLOWED_GITHUB_JOBS = frozenset({"tests", "supporting_tests"})
ALLOWED_GITHUB_EVENTS = frozenset({"pull_request", "push", "workflow_dispatch", "merge_group"})
EXPECTED_WORKFLOW_REF_PREFIX = "kollaborai/kollab/.github/workflows/ci.yml@"


def host_process_scan_environment_errors(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Return missing/mismatched CI metadata names without echoing their values."""
    errors = [name for name, expected in EXPECTED_CI_ENVIRONMENT.items() if environ.get(name) != expected]
    if environ.get("GITHUB_JOB") not in ALLOWED_GITHUB_JOBS:
        errors.append("GITHUB_JOB")
    if environ.get("GITHUB_EVENT_NAME") not in ALLOWED_GITHUB_EVENTS:
        errors.append("GITHUB_EVENT_NAME")
    if not environ.get("GITHUB_WORKFLOW_REF", "").startswith(EXPECTED_WORKFLOW_REF_PREFIX):
        errors.append("GITHUB_WORKFLOW_REF")
    return tuple(errors)


def host_process_scans_allowed(option_enabled: bool, environ: Mapping[str, str]) -> bool:
    """Require both the explicit pytest option and the approved CI job metadata."""
    return option_enabled and not host_process_scan_environment_errors(environ)


_WINDOWS_PATH_MARKER = re.compile(r'''(?:[A-Za-z]:\\|\\\\)''')


def _command_tokens(command: Any) -> list[str] | None:
    if isinstance(command, os.PathLike):
        command = os.fspath(command)
    if isinstance(command, bytes):
        command = os.fsdecode(command)
    if isinstance(command, str):
        try:
            if _WINDOWS_PATH_MARKER.search(command):
                # Non-POSIX shlex accepts unmatched quotes; validate first while
                # retaining Windows backslashes in the tokens used for matching.
                shlex.split(command)
                tokens = shlex.split(command, posix=False)
                return [
                    token[1:-1]
                    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}
                    else token
                    for token in tokens
                ]
            return shlex.split(command)
        except ValueError:
            # An incomplete shell parse is unknown, never evidence of safety.
            return None
    if isinstance(command, Sequence):
        return [_token_text(part) for part in command]
    # Only known argument shapes can be classified. Unknown forms fail closed.
    return None


def _shell_command_tokens(command: Any) -> list[str] | None:
    """Tokenize shell control operators so only command positions are inspected."""
    if isinstance(command, bytes):
        command = os.fsdecode(command)
    if not isinstance(command, str) or _WINDOWS_PATH_MARKER.search(command):
        return _command_tokens(command)
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def _decode_powershell_encoded_command(encoded: str) -> str | None:
    """Decode PowerShell's UTF-16LE base64 command or report an unknown form."""
    try:
        command = base64.b64decode(encoded, validate=True).decode("utf-16-le")
    except ValueError:
        return None
    return command.lstrip("\ufeff")


_POWERSHELL_SAFE_PAYLOADS = frozenset({"get-date"})
_WMIC_SAFE_ALIASES = frozenset({"computersystem"})
_WMIC_SAFE_WQL_CLASSES = frozenset({"win32_computersystem"})
_WMIC_PROCESS_REFERENCE = re.compile(r"\b[\w]*process[\w]*\b", re.IGNORECASE)
_WMIC_WQL_CLASS_REFERENCE = re.compile(
    r"\b(?:from|join)\s+([A-Za-z_][\w.:/\\]*)", re.IGNORECASE
)


def _powershell_payload_requires_fail_closed(command: str) -> bool:
    """Allow only exact, reviewed benign payloads; do not guess at PowerShell semantics."""
    return command.strip().casefold() not in _POWERSHELL_SAFE_PAYLOADS


def _wmic_command_requires_fail_closed(arguments: Sequence[str]) -> bool:
    """Allow only explicit benign WMIC targets; unknown aliases and queries fail closed."""
    if not arguments:
        return True

    joined_arguments = " ".join(arguments)
    if _WMIC_PROCESS_REFERENCE.search(joined_arguments):
        return True

    normalized_arguments = [_shell_token_name(value).casefold() for value in arguments]
    query_requested = any(
        value in {"/query", "-query"} or value.startswith(("/query:", "-query:"))
        for value in normalized_arguments
    )
    if query_requested:
        classes = _WMIC_WQL_CLASS_REFERENCE.findall(joined_arguments)
        normalized_classes = {
            re.split(r"[:/\\]", class_name)[-1].casefold() for class_name in classes
        }
        return not normalized_classes or not normalized_classes <= _WMIC_SAFE_WQL_CLASSES

    targets = [value for value in normalized_arguments if not value.startswith(("/", "-"))]
    if not targets:
        return True
    if targets[0] == "path":
        return len(targets) < 2 or targets[1] not in _WMIC_SAFE_WQL_CLASSES
    return targets[0] not in _WMIC_SAFE_ALIASES


def _token_text(part: Any) -> str:
    if isinstance(part, os.PathLike):
        part = os.fspath(part)
    return os.fsdecode(part) if isinstance(part, bytes) else str(part)


def _shell_token_name(token: str) -> str:
    """Normalize punctuation around commands embedded in shell substitutions."""
    if "$(" in token:
        token = token.rsplit("$(", 1)[-1]
    elif "`" in token:
        token = token.rsplit("`", 1)[-1]
    return token.strip("`$();")


def _executable_name(token: str) -> str:
    """Normalize an executable token across POSIX and Windows spellings."""
    name = PurePath(_shell_token_name(token).replace("\\", "/")).name.casefold()
    return name.removesuffix(".exe")


def _valid_positive_pid_list(value: str) -> bool:
    if not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", value):
        return False
    return all(any(digit != "0" for digit in pid) for pid in value.split(","))


def _lsof_has_only_positive_pid_selectors(options: Sequence[str]) -> bool:
    selectors = 0
    index = 0
    while index < len(options):
        option = _shell_token_name(options[index])
        if option == "-p":
            if index + 1 >= len(options):
                return False
            value = _shell_token_name(options[index + 1])
            if not _valid_positive_pid_list(value):
                return False
            selectors += 1
            index += 2
            continue
        if option.startswith("-p") and not option.startswith("--") and len(option) > 2:
            if not _valid_positive_pid_list(option[2:]):
                return False
            selectors += 1
            index += 1
            continue
        # lsof combines selection criteria in ways that can broaden a PID
        # query (for example `-p 42 -u root`). Keep only the exact PID grammar.
        return False
    return selectors > 0


def _ps_has_only_pid_and_format_options(options: Sequence[str]) -> bool:
    """Accept only a positive PID selector and explicit output-format flags.

    ``ps`` selector semantics vary across Unix implementations. An allowlist is
    safer than trying to enumerate every option that can broaden ``-p``.
    """
    selectors = 0
    index = 0
    while index < len(options):
        option = _shell_token_name(options[index])
        value: str | None = None
        if option in {"-p", "--pid"}:
            if index + 1 >= len(options):
                return False
            value = _shell_token_name(options[index + 1])
            index += 2
        elif option.startswith("--pid="):
            value = option.partition("=")[2]
            index += 1
        elif option.startswith("-p") and not option.startswith("--"):
            value = option[2:]
            index += 1
        elif option in {"-o", "--format"}:
            if index + 1 >= len(options):
                return False
            format_value = _shell_token_name(options[index + 1])
            if not format_value or format_value.startswith("-"):
                return False
            index += 2
            continue
        elif option.startswith("--format="):
            if not option.partition("=")[2]:
                return False
            index += 1
            continue
        else:
            return False
        if not _valid_positive_pid_list(value):
            return False
        selectors += 1
    return selectors > 0


def is_process_table_enumeration_command(command: Any, *, shell: bool = False) -> bool:
    """Fail closed on host process tables; allow only narrow known-safe targets."""
    if isinstance(command, bytes):
        raw_command = os.fsdecode(command)
    else:
        raw_command = command
    if isinstance(raw_command, str):
        for embedded in re.finditer(r"\$\(([^()]*)\)|`([^`]*)`", raw_command):
            nested = embedded.group(1) or embedded.group(2)
            if nested and is_process_table_enumeration_command(nested, shell=True):
                return True
    tokens = _shell_command_tokens(command) if shell else _command_tokens(command)
    if tokens is None:
        return True
    command_positions = {0}
    if shell:
        for index, token in enumerate(tokens[:-1]):
            if token in {";", "&&", "||", "|", "&"}:
                command_positions.add(index + 1)
    for index, token in enumerate(tokens):
        for embedded in re.finditer(r"\$\(([^()]*)\)|`([^`]*)`", token):
            nested = embedded.group(1) or embedded.group(2)
            if nested and is_process_table_enumeration_command(nested, shell=True):
                return True
        if index not in command_positions:
            continue
        name = _executable_name(token)
        if name in {"sh", "bash", "zsh"}:
            shell_args = tokens[index + 1 :]
            for shell_index, option in enumerate(shell_args[:-1]):
                command_option = option in {"-c", "--command"} or (
                    option.startswith("-") and not option.startswith("--") and "c" in option[1:]
                )
                if command_option and is_process_table_enumeration_command(
                    shell_args[shell_index + 1], shell=True
                ):
                    return True
        if name.startswith(("powershell", "pwsh")) and name not in {
            "powershell",
            "pwsh",
        }:
            return True
        if name in {"powershell", "pwsh"}:
            shell_args = tokens[index + 1 :]
            inspected_payload = False
            no_profile = False
            for shell_index, option in enumerate(shell_args):
                option_name = option.casefold()
                if option_name in {"-c", "-command", "-commandwithargs", "-cwa"}:
                    if not no_profile:
                        return True
                    nested_command = " ".join(shell_args[shell_index + 1 :])
                    if (
                        not nested_command.strip()
                        or _powershell_payload_requires_fail_closed(nested_command)
                        or is_process_table_enumeration_command(nested_command, shell=True)
                    ):
                        return True
                    inspected_payload = True
                    break
                if option_name in {"-e", "-ec", "-enc", "-encodedcommand"}:
                    if not no_profile or shell_index + 2 != len(shell_args):
                        return True
                    nested_command = _decode_powershell_encoded_command(shell_args[shell_index + 1])
                    if (
                        nested_command is None
                        or _powershell_payload_requires_fail_closed(nested_command)
                        or is_process_table_enumeration_command(nested_command, shell=True)
                    ):
                        return True
                    inspected_payload = True
                    break
                if option_name == "-noprofile":
                    no_profile = True
                    continue
                if option_name in {"-nologo", "-noninteractive"}:
                    continue
                # Unknown switches, script files, and positional forms are opaque.
                return True
            if not inspected_payload:
                return True
            continue
        if name == "cmd":
            command_args = tokens[index + 1 :]
            for shell_index, option in enumerate(command_args[:-1]):
                if option.casefold() in {"/c", "/k"}:
                    nested_command = command_args[shell_index + 1 :]
                    if len(nested_command) == 1:
                        payload = nested_command[0]
                        if not _WINDOWS_PATH_MARKER.search(payload):
                            nested_command = payload
                        else:
                            executable_ends = list(re.finditer(r"\.exe(?=\s|$)", payload, re.IGNORECASE))
                            if executable_ends:
                                end = executable_ends[-1].end()
                                nested_command = [payload[:end], *shlex.split(payload[end:].strip())]
                    if is_process_table_enumeration_command(nested_command, shell=True):
                        return True
                    break
        docker_args = tokens[index + 1 :]
        docker_exec_offset = (
            2 if name == "docker" and len(docker_args) > 1 and docker_args[0] == "compose" and docker_args[1] == "exec"
            else 1 if name == "docker" and docker_args and docker_args[0] == "exec"
            else 1 if name == "docker-compose" and docker_args and docker_args[0] == "exec"
            else None
        )
        if docker_exec_offset is not None:
            exec_args = docker_args[docker_exec_offset:]
            while exec_args and exec_args[0].startswith("-"):
                option = exec_args[0]
                exec_args = exec_args[1:]
                if option in {"-e", "--env", "-w", "--workdir", "-u", "--user", "--detach-keys"}:
                    if not exec_args:
                        return True
                    exec_args = exec_args[1:]
                elif option.startswith(("--env=", "--workdir=", "--user=", "--detach-keys=")):
                    continue
                elif option not in {"-i", "-t", "-it", "-ti", "-d"}:
                    return True
            if len(exec_args) < 2 or is_process_table_enumeration_command(exec_args[1:], shell=True):
                return True
        if name == "wmic" and _wmic_command_requires_fail_closed(tokens[index + 1 :]):
            return True
        # CIM/WMI accept arbitrary class and WQL query forms. Block these
        # cmdlets wholesale instead of guessing whether a query lists processes.
        if name in {
            "pgrep",
            "pidof",
            "top",
            "htop",
            "pstree",
            "tasklist",
            "get-process",
            "gps",
            "get-ciminstance",
            "gcim",
            "get-wmiobject",
            "gwmi",
        }:
            return True
        if name == "lsof":
            if not _lsof_has_only_positive_pid_selectors(tokens[index + 1 :]):
                return True
        if name != "ps":
            continue

        # Docker's `ps` lists containers, not host processes. Shell command
        # substitution may prefix the token with `$(` in rendered prompt blocks.
        previous_token = _shell_token_name(tokens[index - 1]) if index > 0 else ""
        previous_name = _executable_name(previous_token)
        preceding_pair = (
            tuple(_executable_name(value) for value in tokens[index - 2 : index])
            if index > 1
            else ()
        )
        if previous_name in {"docker", "docker-compose"} or preceding_pair in {
            ("docker", "container"),
            ("docker", "compose"),
        }:
            continue

        if not _ps_has_only_pid_and_format_options(tokens[index + 1 :]):
            return True
    return False


def _path_and_argv(path: Any, argv: Any) -> list[str] | None:
    """Return an executable plus its arguments, or None when it cannot be parsed."""
    if path is None or not isinstance(argv, Sequence) or isinstance(argv, (str, bytes)):
        return None
    executable = _token_text(path)
    arguments = [_token_text(value) for value in argv]
    # The executable path is authoritative; argv[0] is only a display name.
    return [executable, *arguments[1:]] if arguments else [executable]


def _os_process_launch_command(name: str, args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> list[str] | None:
    """Extract the effective executable and argv from public os launch APIs."""
    if name.startswith("posix_spawn"):
        path = args[0] if args else kwargs.get("path")
        argv = args[1] if len(args) > 1 else kwargs.get("argv")
        return _path_and_argv(path, argv)

    if name.startswith("spawn"):
        positional_offset = 1  # mode precedes file/path
        path = args[positional_offset] if len(args) > positional_offset else kwargs.get("file")
        if name[5:6] == "v":
            argv = args[2] if len(args) > 2 else kwargs.get("args")
            return _path_and_argv(path, argv)
        if name[5:6] == "l":
            values = list(args[2:])
            if name.endswith("e") and values:
                values.pop()  # trailing environment mapping in *le/*lpe
            return _path_and_argv(path, values)
        return None

    if name.startswith("exec"):
        path = args[0] if args else kwargs.get("path", kwargs.get("file"))
        if name[4:5] == "v":
            argv = args[1] if len(args) > 1 else kwargs.get("argv", kwargs.get("args"))
            return _path_and_argv(path, argv)
        if name[4:5] == "l":
            values = list(args[1:])
            if name.endswith("e") and values:
                values.pop()  # trailing environment mapping in *le/*lpe
            return _path_and_argv(path, values)
    return None


def guarded_process_enumeration(operation: str, original: Callable[..., Any], *, allowed: bool) -> Callable[..., Any]:
    """Return the original call only when authorized; otherwise fail before it runs."""
    if allowed:
        return original

    @wraps(original)
    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(
            f"host process enumeration blocked for unmarked test operation: {operation}; "
            f"use @{HOST_PROCESS_SCAN_MARKER} only in a sanctioned CI test job"
        )

    return blocked


def install_test_process_scan_guard(
    psutil_module: Any,
    subprocess_module: Any,
    patch: Any,
    *,
    os_module: Any = os,
) -> None:
    """Block common process discovery APIs and command launch wrappers in one test.

    ``Process.children`` uses the platform PID source internally on supported
    psutil versions, so guard it directly as well as the public ``pids`` API.
    Tests that need live process-table access are separately marked and run only
    in the approved CI job; ordinary tests must use exact process handles. This
    does not intercept raw procfs/native access or private psutil implementation
    APIs, and is not a sandbox against code deliberately bypassing it.
    """
    process_type = psutil_module.Process
    for name in ("process_iter", "pids"):
        patch.setattr(
            psutil_module,
            name,
            guarded_process_enumeration(f"psutil.{name}", getattr(psutil_module, name), allowed=False),
        )

    for name in ("children", "parent", "parents"):
        original = getattr(process_type, name, None)
        if not callable(original):
            continue
        patch.setattr(
            process_type,
            name,
            guarded_process_enumeration(f"psutil.Process.{name}", original, allowed=False),
        )

    original_environ = process_type.environ

    @wraps(original_environ)
    def guarded_environ(process: Any, *args: Any, **kwargs: Any) -> Any:
        if getattr(process, "pid", None) != os.getpid():
            raise RuntimeError("process environment reads are restricted to the current test process")
        return original_environ(process, *args, **kwargs)

    patch.setattr(process_type, "environ", guarded_environ)

    original_popen = subprocess_module.Popen
    block_process_table_command = guarded_process_enumeration(
        "subprocess.Popen process-table command", original_popen, allowed=False
    )

    @wraps(original_popen)
    def guarded_popen(*args: Any, **kwargs: Any) -> Any:
        command = args[0] if args else kwargs.get("args")
        executable = kwargs.get("executable")
        if executable is None and len(args) > 2:
            # subprocess.Popen accepts `executable` as its third positional argument.
            executable = args[2]
        executable_command = None
        if executable is not None:
            command_tokens = _command_tokens(command)
            if command_tokens is None:
                return block_process_table_command(*args, **kwargs)
            executable_command = [executable, *command_tokens[1:]]
        shell = bool(kwargs.get("shell", False))
        if is_process_table_enumeration_command(command, shell=shell) or (
            executable_command is not None
            and is_process_table_enumeration_command(executable_command, shell=shell)
        ):
            return block_process_table_command(*args, **kwargs)
        return original_popen(*args, **kwargs)

    patch.setattr(subprocess_module, "Popen", guarded_popen)

    for name in ("system", "popen"):
        original = getattr(os_module, name)
        block_process_table_command = guarded_process_enumeration(
            f"os.{name} process-table command", original, allowed=False
        )

        @wraps(original)
        def guarded_os_command(
            *args: Any,
            _original=original,
            _blocked=block_process_table_command,
            **kwargs: Any,
        ) -> Any:
            command = args[0] if args else kwargs.get("cmd")
            if is_process_table_enumeration_command(command, shell=True):
                return _blocked(*args, **kwargs)
            return _original(*args, **kwargs)

        patch.setattr(os_module, name, guarded_os_command)

    for name in dir(os_module):
        if not name.startswith(("spawn", "posix_spawn", "exec")):
            continue
        original = getattr(os_module, name)
        if not callable(original):
            continue
        block_process_launch = guarded_process_enumeration(f"os.{name} process-table command", original, allowed=False)

        @wraps(original)
        def guarded_os_process_launch(
            *args: Any,
            _name=name,
            _original=original,
            _blocked=block_process_launch,
            **kwargs: Any,
        ) -> Any:
            command = _os_process_launch_command(_name, args, kwargs)
            if command is None or is_process_table_enumeration_command(command):
                return _blocked(*args, **kwargs)
            return _original(*args, **kwargs)

        patch.setattr(os_module, name, guarded_os_process_launch)
