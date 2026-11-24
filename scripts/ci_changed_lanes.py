"""Pick which optional CI lanes a change needs.
Webui skips only when all paths are safe; voice runs for voice paths, shared files, or empty input.
"""

import sys

VOICE_PREFIX = "packages/kollabor-voice/"
SHARED_FILES = {".github/workflows/ci.yml", "uv.lock", "pyproject.toml"}
SHARED_PREFIXES = (".github/actions/",)
WEBUI_SAFE_PREFIXES = (
    "docs/",
    "changes/",
    VOICE_PREFIX,
    "tests/unit/",
    "tests/tmux/specs/",
    ".github/ISSUE_TEMPLATE/",
)


def webui_path_is_safe(path: str) -> bool:
    """Return whether a changed path cannot affect the webui lane."""
    if path.startswith("tests/unit/") and "webui" in path.casefold():
        return False
    return path.startswith(WEBUI_SAFE_PREFIXES) or (
        "/" not in path and path.endswith(".md")
    )


def lanes_for(paths: list[str]) -> dict[str, bool]:
    """Return which lanes must run for these changed paths."""
    if not paths:
        return {"webui": True, "voice": True}
    shared = any(p in SHARED_FILES or p.startswith(SHARED_PREFIXES) for p in paths)
    return {
        "webui": not all(webui_path_is_safe(p) for p in paths),
        "voice": shared or any(p.startswith(VOICE_PREFIX) for p in paths),
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--all" in args:
        paths = []
    else:
        paths = [line.strip() for line in sys.stdin if line.strip()]
    lanes = lanes_for(paths)
    for name in ("webui", "voice"):
        print(f"{name}={str(lanes[name]).lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
