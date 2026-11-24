"""Pick which optional CI lanes a change needs.

Reads changed paths from stdin, one per line, and prints one line per lane
for GITHUB_OUTPUT: ``webui=true|false`` and ``voice=true|false``. An empty
path list, or ``--all``, turns both lanes on.
"""

import sys

WEBUI_PREFIX = "packages/kollabor-webui/"
VOICE_PREFIX = "packages/kollabor-voice/"
SHARED_FILES = {".github/workflows/ci.yml", "uv.lock", "pyproject.toml"}
SHARED_PREFIXES = (".github/actions/",)


def lanes_for(paths: list[str]) -> dict[str, bool]:
    """Return which lanes must run for these changed paths."""
    if not paths:
        return {"webui": True, "voice": True}
    shared = any(p in SHARED_FILES or p.startswith(SHARED_PREFIXES) for p in paths)
    return {
        "webui": shared or any(p.startswith(WEBUI_PREFIX) for p in paths),
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
