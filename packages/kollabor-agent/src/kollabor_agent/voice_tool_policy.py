"""Fail-closed execution guard for trusted LiveKit-originated tool calls.

This is the low-level executor boundary. It does not implement the voice
confirmation exchange; mutating or externally communicative actions remain
blocked with ``confirmation_required`` until that control-plane contract is
connected.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .voice_admission import validate_voice_admission_context

VOICE_TOOL_POLICY_ID = "voice_terminal_hub_v1"
MAX_VOICE_COMMAND_BYTES = 8 * 1024
MAX_VOICE_OUTPUT_BYTES = 8 * 1024
MAX_VOICE_COMMAND_SECONDS = 60

_ALLOWED_VOICE_TOOL_TYPES = frozenset(
    {"terminal", "hub_status", "hub_capture", "hub_msg"}
)
_TRUSTED_TERMINAL_EXECUTABLES = frozenset(
    {"cat", "git", "grep", "head", "ls", "pwd", "rg", "tail"}
)
_LS_DEREFERENCE_FLAGS = frozenset(
    {"--dereference", "--dereference-command-line", "--follow-symlinks", "--recursive"}
)
_SHELL_OPERATOR_CHARS = frozenset(";|&<>$`\n\r")
_READ_ONLY_GIT_COMMANDS = frozenset({"status", "diff", "log", "show"})
_READ_ONLY_SEARCH_FLAGS = frozenset(
    {
        "-n",
        "--line-number",
        "-i",
        "--ignore-case",
        "-F",
        "--fixed-strings",
        "-v",
        "--invert-match",
        "-w",
        "--word-regexp",
        "--hidden",
        "--no-heading",
    }
)
_HARD_BLOCKED_COMMANDS = frozenset(
    {
        "sudo",
        "su",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "kill",
        "killall",
        "pkill",
        "launchctl",
        "systemctl",
        "service",
    }
)
_HARD_BLOCKED_GIT_COMMANDS = frozenset(
    {
        "push",
        "tag",
        "reset",
        "clean",
        "checkout",
        "restore",
        "switch",
        "merge",
        "rebase",
        "cherry-pick",
    }
)
_HARD_BLOCKED_GIT_FLAGS = frozenset(
    {"--output", "--ext-diff", "--textconv", "--external-diff"}
)


@dataclass(frozen=True, slots=True)
class VoiceTerminalPlan:
    """Canonical, shell-free read-only terminal action."""

    plan_id: str
    digest: str
    classification: str
    argv: tuple[str, ...]
    canonical_cwd: Path


@dataclass(frozen=True, slots=True)
class VoiceToolDecision:
    """Decision made before any PermissionManager or process side effect."""

    allowed: bool
    reason_code: str | None = None
    message: str | None = None
    plan: VoiceTerminalPlan | None = None


class VoiceToolExecutionGuard:
    """Enforce the fixed voice tool allowlist and terminal safety boundary."""

    def evaluate(
        self,
        tool_data: Mapping[str, Any],
        execution_context: Mapping[str, Any] | None,
        *,
        repository_root: str | Path | None,
    ) -> VoiceToolDecision:
        if not isinstance(execution_context, Mapping) or execution_context.get("source") != "livekit_voice":
            return VoiceToolDecision(allowed=True)
        if not validate_voice_admission_context(execution_context):
            return self._deny("command_blocked", "Voice origin lacks a valid server admission capability.")
        if not self._has_valid_binding_context(execution_context):
            return self._deny("command_blocked", "Voice tool context is incomplete or unsupported.")

        raw_tool_type = tool_data.get("type", "")
        tool_type = str(raw_tool_type).lower().replace("-", "_")
        if tool_type == "git":
            tool_type = "terminal"
        if tool_type not in _ALLOWED_VOICE_TOOL_TYPES:
            return self._deny("command_blocked", "This tool is not allowed for voice-originated turns.")
        if tool_type in {"hub_status", "hub_capture"}:
            return VoiceToolDecision(allowed=True)
        if tool_type == "hub_msg":
            return self._deny(
                "confirmation_required",
                "Sending a hub message requires a separate spoken confirmation.",
            )
        return self._evaluate_terminal(tool_data, execution_context, repository_root)

    @staticmethod
    def _has_valid_binding_context(context: Mapping[str, Any]) -> bool:
        generation = context.get("generation")
        return (
            context.get("voice_tool_policy") == VOICE_TOOL_POLICY_ID
            and isinstance(context.get("voice_session_id"), str)
            and bool(context.get("voice_session_id"))
            and isinstance(context.get("turn_id"), str)
            and bool(context.get("turn_id"))
            and isinstance(generation, int)
            and not isinstance(generation, bool)
            and generation > 0
        )

    def _evaluate_terminal(
        self,
        tool_data: Mapping[str, Any],
        context: Mapping[str, Any],
        repository_root: str | Path | None,
    ) -> VoiceToolDecision:
        command = tool_data.get("command")
        if not isinstance(command, str) or not command.strip():
            return self._deny("unsafe_syntax", "Voice terminal command is empty or malformed.")
        try:
            command_bytes = command.encode("utf-8")
        except UnicodeEncodeError:
            return self._deny("unsafe_syntax", "Voice terminal command is not valid UTF-8.")
        if len(command_bytes) > MAX_VOICE_COMMAND_BYTES:
            return self._deny("unsafe_syntax", "Voice terminal command exceeds the 8 KiB limit.")
        if any(char in _SHELL_OPERATOR_CHARS for char in command):
            return self._deny("unsafe_syntax", "Compound shell syntax is not supported for voice commands.")
        if tool_data.get("background"):
            return self._deny("command_blocked", "Background terminal sessions are not allowed by voice policy.")

        try:
            argv = tuple(shlex.split(command, posix=True))
        except ValueError:
            return self._deny("unsafe_syntax", "Voice terminal command could not be parsed safely.")
        if not argv or any(not part or "\\" in part for part in argv):
            return self._deny("unsafe_syntax", "Voice terminal command contains unsupported escaping.")
        if _is_path_qualified_executable(argv[0]):
            return self._deny(
                "command_blocked",
                "Voice terminal executables must use a trusted bare command name.",
            )

        root = Path(repository_root or Path.cwd()).expanduser().resolve()
        requested_cwd = Path(str(tool_data.get("cwd") or root)).expanduser()
        if not requested_cwd.is_absolute():
            requested_cwd = root / requested_cwd
        try:
            canonical_cwd = requested_cwd.resolve(strict=False)
        except (OSError, RuntimeError):
            return self._deny("unsafe_syntax", "Voice terminal working directory is invalid.")
        if not _is_relative_to(canonical_cwd, root):
            return self._deny("unsafe_syntax", "Voice terminal working directory must stay inside the project.")
        if _has_unsafe_ls_flags(argv):
            return self._deny(
                "unsafe_syntax",
                "Recursive or symlink-dereferencing ls options are not allowed for voice commands.",
            )

        if _is_hard_blocked(argv):
            return self._deny(
                "command_blocked",
                "This terminal action is permanently blocked for voice-originated turns.",
            )

        if not _is_read_only_action(argv, canonical_cwd, root):
            return self._deny(
                "confirmation_required",
                "This terminal action requires an explicit voice confirmation before execution.",
            )

        trusted_executable = _resolve_trusted_executable(argv[0], root)
        if trusted_executable is None:
            return self._deny(
                "command_blocked",
                "Voice terminal executable is unavailable in the fixed trusted command path.",
            )
        normalized_argv = (str(trusted_executable), *argv[1:])
        plan_material = {
            "voice_session_id": context["voice_session_id"],
            "generation": context["generation"],
            "turn_id": context["turn_id"],
            "cwd": str(canonical_cwd),
            "argv": normalized_argv,
        }
        digest = sha256(
            json.dumps(plan_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        plan = VoiceTerminalPlan(
            plan_id=f"voice-plan-{digest[:20]}",
            digest=digest,
            classification="read_only",
            argv=normalized_argv,
            canonical_cwd=canonical_cwd,
        )
        return VoiceToolDecision(allowed=True, plan=plan)

    @staticmethod
    def _deny(reason_code: str, message: str) -> VoiceToolDecision:
        return VoiceToolDecision(allowed=False, reason_code=reason_code, message=message)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_path_qualified_executable(executable: str) -> bool:
    return (
        Path(executable).name != executable
        or "/" in executable
        or "\\\\" in executable
    )


def _has_unsafe_ls_flags(argv: Sequence[str]) -> bool:
    if not argv or Path(argv[0]).name.lower() != "ls":
        return False
    for argument in argv[1:]:
        lowered = argument.lower()
        if lowered in _LS_DEREFERENCE_FLAGS:
            return True
        if argument.startswith("-") and not argument.startswith("--"):
            if any(flag in argument[1:] for flag in "RLH"):
                return True
    return False


def _resolve_trusted_executable(executable: str, repository_root: Path) -> Path | None:
    if _is_path_qualified_executable(executable):
        return None
    if executable not in _TRUSTED_TERMINAL_EXECUTABLES:
        return None
    # os.defpath is a fixed system search path, not the user/session PATH; it
    # cannot prefer a repository-local or /tmp executable placed earlier in PATH.
    resolved = shutil.which(executable, path=os.defpath)
    if not resolved:
        return None
    try:
        canonical = Path(resolved).resolve(strict=True)
        if not canonical.is_file() or not os.access(canonical, os.X_OK):
            return None
        if _is_relative_to(canonical, repository_root):
            return None
        for parent in (canonical, *canonical.parents):
            if parent.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                return None
    except (OSError, RuntimeError):
        return None
    return canonical


def _is_hard_blocked(argv: Sequence[str]) -> bool:
    tokens = tuple(token.lower() for token in argv)
    executable = Path(tokens[0]).name
    if executable in _HARD_BLOCKED_COMMANDS:
        return True
    if any(token in {"--restart", "--update", "--reset-config"} for token in tokens):
        return True
    if executable == "rm":
        for token in tokens[1:]:
            if token == "--recursive" or (token.startswith("-") and ("r" in token[1:] or "f" in token[1:])):
                return True
    if executable == "git" and len(tokens) > 1:
        if tokens[1] in _HARD_BLOCKED_GIT_COMMANDS:
            return True
        if any(token.split("=", 1)[0] in _HARD_BLOCKED_GIT_FLAGS for token in tokens[1:]):
            return True
        if tokens[1] == "branch" and any(token in {"-d", "-D", "--delete"} for token in tokens[2:]):
            return True
    if executable == "gh" and tokens[1:3] == ("pr", "merge"):
        return True
    if executable == "brew" and len(tokens) > 2 and tokens[1] == "services":
        if tokens[2] in {"install", "uninstall", "start", "stop", "restart", "cleanup"}:
            return True
    if executable == "kollab" and "--hub" in tokens:
        hub_index = tokens.index("--hub")
        if tokens[hub_index + 1 : hub_index + 2] and tokens[hub_index + 1] in {"stop", "kill"}:
            return True
    return False


def _is_read_only_action(argv: Sequence[str], cwd: Path, root: Path) -> bool:
    tokens = tuple(token.lower() for token in argv)
    executable = Path(tokens[0]).name
    if executable == "pwd":
        return len(argv) == 1
    if executable == "ls":
        return all(_path_is_inside(token, cwd, root) for token in argv[1:] if not token.startswith("-"))
    if executable in {"cat", "head", "tail"}:
        paths = [token for token in argv[1:] if not token.startswith("-")]
        return bool(paths) and all(_path_is_inside(token, cwd, root) for token in paths)
    if executable in {"rg", "grep"}:
        positional: list[str] = []
        for token in argv[1:]:
            if token in _READ_ONLY_SEARCH_FLAGS:
                continue
            if token.startswith("-"):
                return False
            positional.append(token)
        if not positional:
            return False
        return all(_path_is_inside(token, cwd, root) for token in positional[1:])
    if executable == "git" and len(tokens) > 1 and tokens[1] in _READ_ONLY_GIT_COMMANDS:
        if any(token.split("=", 1)[0] in _HARD_BLOCKED_GIT_FLAGS for token in tokens[2:]):
            return False
        if "--" in argv:
            separator = argv.index("--")
            return all(_path_is_inside(token, cwd, root) for token in argv[separator + 1 :])
        return True
    if executable == "kollab" and tokens[1:3] in {("--hub", "status"), ("--hub", "capture")}:
        return True
    return False


def _path_is_inside(value: str, cwd: Path, root: Path) -> bool:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        return _is_relative_to(candidate.resolve(strict=False), root)
    except (OSError, RuntimeError):
        return False
