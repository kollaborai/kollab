"""Windows shell smoke: one command per resolved shell, plus the rendered base prompt.

Runs on the windows-latest runner (windows-smoke). Fails when the command fails,
the rendered prompt contains "[trender error", or the `shell:` line does not name
the expected shell.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from kollabor_agent.shell_executor import ShellExecutor
from kollabor_agent.shell_utils import agent_shell
from kollabor_ai.prompt_renderer import render_system_prompt

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = ROOT / "bundles" / "agents" / "_base" / "base_prompt.md"
# The rendered `  shell:` line of the session block names these per shell kind.
SHELL_LABELS = {
    "git-bash": "Git Bash",
    "pwsh": "PowerShell 7",
    "powershell": "Windows PowerShell 5.1",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """The argparse conventions of windows_halt_smoke.py."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--expect",
        required=True,
        choices=sorted(SHELL_LABELS),
        help="the kind agent_shell() must return",
    )
    parser.add_argument(
        "--command",
        required=True,
        help="the command string to run through ShellExecutor",
    )
    parser.add_argument(
        "--prompt", default=str(DEFAULT_PROMPT), help="the prompt file to render"
    )
    return parser.parse_args(argv)


def find_trender_errors(rendered: str) -> list[str]:
    """Every rendered line carrying a `[trender error` marker."""
    return [line for line in rendered.splitlines() if "[trender error" in line]


def shell_line_names(rendered: str, expect: str) -> bool:
    """Whether a `  shell:` line of `rendered` names the shell kind `expect`."""
    label = SHELL_LABELS.get(expect, "")
    return any(
        line.startswith("  shell:") and label in line for line in rendered.splitlines()
    )


async def _async_main(args: argparse.Namespace) -> int:
    shell = agent_shell()
    if shell.kind != args.expect:
        print(
            f"FAIL: resolved {shell.path} kind {shell.kind}, expected {args.expect}",
            file=sys.stderr,
        )
        return 1

    result = await ShellExecutor().run(args.command, timeout=60)
    if not result.success or not result.stdout.strip():
        print(
            f"FAIL: command failed (exit {result.exit_code}): {result.stderr.strip()}",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {result.stdout.strip().splitlines()[0]}")

    prompt = Path(args.prompt)
    text = prompt.read_text(encoding="utf-8")
    rendered = render_system_prompt(text, timeout=30, base_path=prompt.parent)

    errors = find_trender_errors(rendered)
    if errors:
        for line in errors:
            print(
                f"FAIL: trender error in rendered prompt: {line}",
                file=sys.stderr,
            )
        return 1

    # On POSIX the session block prints the shell path, so no label to match.
    if shell.kind != "posix" and not shell_line_names(rendered, args.expect):
        print(
            f"FAIL: the shell: line does not name {shell.label}",
            file=sys.stderr,
        )
        return 1

    print(f"PASS: {shell.kind} {shell.label} command-ok prompt-ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
