"""Fail-closed execution guard for trusted LiveKit-originated tool calls.

This is the low-level executor boundary. A mutating or externally communicative
action comes back ``confirmation_required``: the agent asks for the user's OK in
its reply, and their yes runs that exact call (``yes_approval``), planned again
as approved: argv with no shell, past the same hard blocks. A yes runs only what
``_voice_may_run`` lists; anything else comes back ``not_by_voice``.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .voice_admission import is_voice_origin, validate_voice_origin

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
        "-r",  # grep: recursive, never following a symlink it meets (rg: --replace, output only)
        "--recursive",
    }
)
_READ_ONLY_SEARCH_FILTERS = ("--include=", "--exclude=", "--exclude-dir=", "--glob=")
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
# They run whatever their arguments say, so the checks on the first word never
# see the real command: `bash -c '...'`, `env rm ...`, `python3 -c '...'`.
_INTERPRETERS_AND_WRAPPERS = frozenset(
    {
        "sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh",
        "env", "xargs", "timeout", "gtimeout", "nice", "nohup", "exec", "eval",
        "command", "builtin", "time", "caffeinate",
        "node", "nodejs", "deno", "bun", "php", "lua", "osascript", "awk", "gawk", "nawk",
        "npx", "bunx", "uvx", "pipx",
    }
)
_VERSIONED_INTERPRETER = re.compile(r"(?:python|pypy|perl|ruby|node|php|lua)[0-9.]*w?")
_FIND_RUNS_OR_DELETES = frozenset({"-exec", "-execdir", "-ok", "-okdir", "-delete"})
# git's options before the subcommand. -c, --config-env and --exec-path set config
# or the helper path, and config can make a pager, an alias or a hook run anything.
_GIT_GLOBAL_OPTIONS_WITH_VALUE = frozenset(
    {"-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--attr-source", "--list-cmds"}
)
_GIT_CONFIG_OPTIONS = frozenset({"-c", "--config-env", "--exec-path"})
# The ones that point git at a directory: it must stay inside the project, like the cwd.
_GIT_PATH_OPTIONS = frozenset({"-C", "--git-dir", "--work-tree"})
# The git subcommands a yes may run. Anything else is blocked: the ones that drop
# work or history (push, reset, stash...), config, and any alias or git-* helper,
# which runs whatever its own config says.
_VOICE_GIT_COMMANDS = frozenset(
    {
        "add", "blame", "branch", "commit", "describe", "diff", "fetch", "grep",
        "log", "ls-files", "mv", "rev-parse", "rm", "shortlog", "show", "status",
    }
)
# Where git takes a remote, `<helper>::<address>` runs the git-remote-<helper> program
# (`ext::` runs any command). Only there: a commit message may say `Parser::parse`.
_GIT_REMOTE_COMMANDS = frozenset({"fetch", "pull", "push", "clone", "ls-remote", "remote", "submodule", "archive"})
_GIT_REMOTE_HELPER = re.compile(r"(?:^|=)[a-z0-9][a-z0-9+.-]*::")
_HARD_BLOCKED_GIT_FLAGS = frozenset(
    {
        "--output",
        "--ext-diff",
        "--textconv",
        "--external-diff",
        "--upload-pack",
        "--receive-pack",
        "--exec",
        "--open-files-in-pager",
    }
)
# What a spoken yes may run besides the read-only commands. The user heard only the
# agent's words for the call, so never a tool that runs code from its arguments (ex,
# sqlite3, sed...): git (its subcommands are checked in _is_hard_blocked), file changes
# inside the project with only these options, the project's own make and npm scripts,
# and read-only gh and kollab.
_VOICE_FILE_OPTIONS = {
    "mkdir": frozenset({"-p", "-v"}),
    "touch": frozenset(),
    "cp": frozenset({"-R", "-r", "-p", "-n", "-v"}),
    "mv": frozenset({"-n", "-v"}),
    "rm": frozenset({"-d", "-v"}),
}
_VOICE_GH_COMMANDS = frozenset(
    {
        ("--version",), ("version",), ("status",),
        ("pr", "list"), ("pr", "view"), ("pr", "status"), ("pr", "checks"), ("pr", "diff"),
        ("issue", "list"), ("issue", "view"), ("issue", "status"),
        ("repo", "view"), ("run", "list"), ("run", "view"),
    }
)
_VOICE_KOLLAB_HUB_COMMANDS = frozenset({"status", "capture", "where", "help"})
# package.json's scripts and the dependencies it lists, never npm exec (npx), which
# fetches and runs any package.
_VOICE_NPM_COMMANDS = frozenset({"run", "test", "start", "install", "ci"})
# The voice list in words, for the model to offer in place of what was refused.
# Keep it in step with _voice_may_run.
_VOICE_CAN = (
    "By voice, the terminal tool runs read-only commands, and with the user's spoken yes also git, "
    "the project's own make and npm scripts, read-only gh and kollab --hub commands, and file changes "
    "inside the project (touch for a new empty file, mkdir, cp, mv, rm). Writing text into a file "
    "needs a typed message."
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
        approved: bool = False,
    ) -> VoiceToolDecision:
        """``approved``: the user's yes OK'd this call; it still passes every hard block."""
        if not is_voice_origin(execution_context):
            return VoiceToolDecision(allowed=True)
        if not validate_voice_origin(execution_context):
            return self._deny("command_blocked", "Voice origin lacks a valid server admission capability.")
        if not self._has_valid_binding_context(execution_context):
            return self._deny("command_blocked", "Voice tool context is incomplete or unsupported.")

        raw_tool_type = tool_data.get("type", "")
        tool_type = str(raw_tool_type).lower().replace("-", "_")
        if tool_type == "git":
            tool_type = "terminal"
        if tool_type not in _ALLOWED_VOICE_TOOL_TYPES:
            # Models reach for file_create and the like first: without the voice list
            # the agent says it can't do what the terminal does on a yes.
            return self._deny(
                "command_blocked",
                f"This tool does not run by voice. {_VOICE_CAN} If one of those does what the user "
                "asked, call the terminal tool with it. Otherwise tell the user it was not run, and "
                "offer to do it from a typed message.",
            )
        if tool_type in {"hub_status", "hub_capture"}:
            return VoiceToolDecision(allowed=True)
        if tool_type == "hub_msg":
            if approved:
                return VoiceToolDecision(allowed=True)
            return self._deny(
                "confirmation_required",
                "Sending a hub message requires a separate spoken confirmation.",
            )
        return self._evaluate_terminal(tool_data, execution_context, repository_root, approved)

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
        approved: bool = False,
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
        if _git_leaves_project(argv, canonical_cwd, root):
            return self._deny("command_blocked", "Voice git commands must stay inside the project.")
        read_only = _is_read_only_action(argv, canonical_cwd, root)
        if not read_only and not _voice_may_run(argv, canonical_cwd, root):
            return self._deny(
                "not_by_voice",
                f"This command does not run by voice. {_VOICE_CAN} Tell the user it was not run, "
                "and offer to run it if they ask in a typed message, where they see the exact command.",
            )

        if approved:
            # The user's yes: whatever it names on PATH, never one in the project
            # or in a directory others can write.
            trusted_executable = _resolve_executable(argv[0], root, os.environ.get("PATH") or os.defpath)
        else:
            trusted_executable = _resolve_trusted_executable(argv[0], root) if read_only else None
            if trusted_executable is None:  # not read-only, or its tool is off the fixed system path
                return self._deny(
                    "confirmation_required",
                    "This terminal action requires an explicit voice confirmation before execution.",
                )
        if trusted_executable is None:
            return self._deny(
                "command_blocked",
                "Voice terminal executable was not found, or is in the project or a directory others can write.",
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
            classification="approved" if approved else "read_only",
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
    if executable not in _TRUSTED_TERMINAL_EXECUTABLES:
        return None
    # os.defpath is a fixed system search path, not the user/session PATH; it
    # cannot prefer a repository-local or /tmp executable placed earlier in PATH.
    return _resolve_executable(executable, repository_root, os.defpath)


# Group-writable is fine for root/wheel and macOS admin: Homebrew's Cellar is.
_ADMIN_GROUPS = frozenset({0, 80})


def _resolve_executable(executable: str, repository_root: Path, search_path: str) -> Path | None:
    """The executable a shell would run, or None when it is one nobody should trust:
    inside the project, or under a directory someone else can write."""
    if _is_path_qualified_executable(executable):
        return None
    resolved = shutil.which(executable, path=search_path)
    if not resolved:
        return None
    try:
        canonical = Path(resolved).resolve(strict=True)
        if not canonical.is_file() or not os.access(canonical, os.X_OK):
            return None
        if _is_relative_to(canonical, repository_root):
            return None
        for parent in (canonical, *canonical.parents):
            info = parent.stat()
            if info.st_uid not in (0, os.getuid()) or info.st_mode & stat.S_IWOTH:
                return None
            if info.st_mode & stat.S_IWGRP and info.st_gid not in _ADMIN_GROUPS:
                return None
    except (OSError, RuntimeError):
        return None
    return canonical


def _git_leaves_project(argv: Sequence[str], cwd: Path, root: Path) -> bool:
    """git sent to a directory outside the project (-C, --git-dir, --work-tree)."""
    if Path(argv[0]).name.lower() != "git":
        return False
    base, at = cwd, 1
    while at < len(argv) and argv[at].startswith("-"):
        option, has_value, value = argv[at].partition("=")
        if option in _GIT_GLOBAL_OPTIONS_WITH_VALUE and not has_value:
            at += 1
            value = argv[at] if at < len(argv) else ""
        if option in _GIT_PATH_OPTIONS:
            if not _path_is_inside(value, base, root):
                return True
            if option == "-C" and value:  # later paths count from there
                base = (base / Path(value).expanduser()).resolve(strict=False)
        at += 1
    return False


def _git_subcommand(argv: Sequence[str]) -> int | None:
    """Where git's subcommand is, past its global options; None when one sets config."""
    at = 1
    while at < len(argv) and argv[at].startswith("-"):
        option, has_value = argv[at].split("=", 1)[0], "=" in argv[at]
        if option in _GIT_CONFIG_OPTIONS:
            return None
        at += 2 if option in _GIT_GLOBAL_OPTIONS_WITH_VALUE and not has_value else 1
    return at


def _is_hard_blocked(argv: Sequence[str]) -> bool:
    tokens = tuple(token.lower() for token in argv)
    executable = Path(tokens[0]).name
    if executable in _HARD_BLOCKED_COMMANDS:
        return True
    if executable in _INTERPRETERS_AND_WRAPPERS or _VERSIONED_INTERPRETER.fullmatch(executable):
        return True
    if any(token in {"--restart", "--update", "--reset-config"} for token in tokens):
        return True
    if executable == "rm":
        for token in tokens[1:]:
            if token in {"--recursive", "--force"} or (
                token.startswith("-") and not token.startswith("--") and ("r" in token[1:] or "f" in token[1:])
            ):
                return True
    if executable == "find" and any(token in _FIND_RUNS_OR_DELETES for token in tokens[1:]):
        return True
    if executable == "git" and len(argv) > 1:
        at = _git_subcommand(argv)
        if at is None:
            return True
        if at >= len(argv):
            return False  # bare `git` (or only global options): prints its help
        subcommand, rest = tokens[at], tokens[at + 1 :]
        if subcommand not in _VOICE_GIT_COMMANDS:
            return True
        if any(token.split("=", 1)[0] in _HARD_BLOCKED_GIT_FLAGS for token in rest):
            return True
        if subcommand in _GIT_REMOTE_COMMANDS and any(_GIT_REMOTE_HELPER.search(token) for token in rest):
            return True
        if subcommand == "grep" and any(
            "O" in token for token in argv[at + 1 :] if token.startswith("-") and not token.startswith("--")
        ):
            return True  # -O<pager>: opens the matches in that program
        if subcommand == "branch" and any(token in {"-d", "--delete"} for token in rest):
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
            if token in _READ_ONLY_SEARCH_FLAGS or token.startswith(_READ_ONLY_SEARCH_FILTERS):
                continue
            if re.fullmatch(r"-[A-Za-z]{2,}", token) and all(f"-{c}" in _READ_ONLY_SEARCH_FLAGS for c in token[1:]):
                continue  # "-rn": those short flags written together
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


def _voice_may_run(argv: Sequence[str], cwd: Path, root: Path) -> bool:
    """A command a spoken yes may run, past the hard blocks: see _VOICE_FILE_OPTIONS."""
    executable = Path(argv[0]).name.lower()
    if executable == "git":
        return True
    if executable in _VOICE_FILE_OPTIONS:
        paths = [token for token in argv[1:] if token not in _VOICE_FILE_OPTIONS[executable]]
        return bool(paths) and all(
            not token.startswith("-") and _is_project_file(token, cwd, root) for token in paths
        )
    if executable == "gh":
        return tuple(argv[1:2]) in _VOICE_GH_COMMANDS or tuple(argv[1:3]) in _VOICE_GH_COMMANDS
    if executable == "kollab":
        return len(argv) > 2 and argv[1] == "--hub" and argv[2] in _VOICE_KOLLAB_HUB_COMMANDS
    if executable == "make":
        return _make_runs_project_rules(argv[1:], cwd, root)
    if executable == "npm":
        args = argv[1:]
        if not args or args[0] not in _VOICE_NPM_COMMANDS or any(token.startswith("-") for token in args):
            return False
        return len(args) <= 2 if args[0] == "run" else len(args) == 1  # no package, no extra argument
    return False


def _make_runs_project_rules(args: Sequence[str], cwd: Path, root: Path) -> bool:
    """Targets of the project's own makefile, with -C and -f only inside the project.

    Never VAR=value or another option (--eval...): they put the caller's own commands
    in the recipes. make applies every -C before it reads a makefile, so -f counts
    from the last one.
    """
    directory, makefiles, at = cwd, [], 0
    while at < len(args):
        token = args[at]
        if token in {"-C", "-f"} and at + 1 < len(args):
            value = args[at + 1]
            if token == "-f":
                makefiles.append(value)
            elif not _is_project_file(value, directory, root):
                return False
            else:
                directory = (directory / Path(value).expanduser()).resolve(strict=False)
            at += 2
        elif token.startswith("-") or "=" in token:
            return False
        else:
            at += 1
    return all(_is_project_file(name, directory, root) for name in makefiles)


def _is_project_file(value: str, cwd: Path, root: Path) -> bool:
    """Inside the project and outside .git, where a hook or config runs code."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        relative = candidate.resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False
    return ".git" not in (part.lower() for part in relative.parts)  # .GIT on a case-blind disk


def _path_is_inside(value: str, cwd: Path, root: Path) -> bool:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        return _is_relative_to(candidate.resolve(strict=False), root)
    except (OSError, RuntimeError):
        return False
