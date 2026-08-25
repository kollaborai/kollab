"""Acceptance check for the byte-stable cacheable system-prompt prefix.

Renders the installed `_base/base_prompt.md` from two simulated cold sessions
with different cwd + git state. With stable_prefix on (skip_volatile=True) the
rendered prefix MUST be byte-identical (that empty diff is what lets oMLX reuse
its on-disk KV cache across sessions). Also asserts the gate is doing real work
(un-stripped renders DO differ) and that no content is silently dropped
(session-context still renders when not stripped).

Run: pytest tests/fixes/test_stable_prefix.py  — or: python tests/fixes/test_stable_prefix.py
"""

import os
import subprocess
import tempfile
from pathlib import Path

from kollabor_ai.prompt_renderer import render_system_prompt

BASE = Path.home() / ".kollab" / "agents" / "_base"
BASE_PROMPT = BASE / "base_prompt.md"


def _render(cwd: Path, skip: bool) -> str:
    old = os.getcwd()
    os.chdir(cwd)
    try:
        return render_system_prompt(
            BASE_PROMPT.read_text(encoding="utf-8"),
            base_path=BASE,
            skip_volatile=skip,
        )
    finally:
        os.chdir(old)


def _mkrepo(path: str, name: str) -> Path:
    d = Path(path)
    subprocess.run(["git", "init", "-q"], cwd=d, check=False)
    (d / f"{name}.txt").write_text(name)  # distinct dirty git state per session
    return d


def test_stable_prefix_byte_identical_across_sessions() -> None:
    assert BASE_PROMPT.exists(), f"installed base prompt missing: {BASE_PROMPT}"

    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        da, db = _mkrepo(a, "alpha"), _mkrepo(b, "beta")

        # Different cwd + different git state. skip_volatile strips the only
        # cwd/git/time-dependent block (session-context) -> identical prefix.
        stable_a = _render(da, True)
        stable_b = _render(db, True)
        assert stable_a == stable_b, (
            "STABLE PREFIX DIVERGED across sessions — oMLX cache would miss.\n"
            f"len_a={len(stable_a)} len_b={len(stable_b)}"
        )

        # The gate is doing real work: un-stripped renders DO differ (pwd/git).
        vol_a = _render(da, False)
        vol_b = _render(db, False)
        assert vol_a != vol_b, "un-stripped renders identical — test not exercising volatility"

        # Nothing dropped: session-context is present un-stripped, absent stable.
        assert "session context" in vol_a.lower()
        assert "session context" not in stable_a.lower()
        # And no literal trender tags leak into either output.
        assert "<trender" not in stable_a
        assert "<trender" not in vol_a


if __name__ == "__main__":
    test_stable_prefix_byte_identical_across_sessions()
    print("OK: stable prefix byte-identical across sessions; volatile content preserved")
