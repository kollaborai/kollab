"""Import every kollab module and report the ones that fail on this platform (kollab #268).

Windows CI runs it. A module that imports a Unix-only stdlib module, or evaluates a Unix-only os
or signal name at import time, fails here. A missing optional package is listed, not failed.
"""

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIRS = sorted(ROOT.glob("packages/*/src"))
# The relay server runs only on its Linux host (docs/specs/windows-platform-helpers.md).
SKIP = {
    "plugins.hub.relay_backend",
    "plugins.hub.relay_runtime",
    "plugins.hub.relay_selfhost",
    "plugins.hub.relay_service",
}
UNIX_ONLY = {
    "crypt",
    "curses",
    "fcntl",
    "grp",
    "nis",
    "posix",
    "pty",
    "pwd",
    "readline",
    "resource",
    "spwd",
    "syslog",
    "termios",
    "tty",
}


def module_names() -> list[str]:
    tops = [(ROOT, ROOT / "kollabor"), (ROOT, ROOT / "plugins")]
    tops += [
        (src, pkg)
        for src in SRC_DIRS
        for pkg in sorted(src.iterdir())
        if (pkg / "__init__.py").is_file()
    ]
    names = []
    for base, top in tops:
        for path in sorted(top.rglob("*.py")):
            parts = path.relative_to(base).with_suffix("").parts
            if "tests" in parts or parts[-1] == "__main__":
                continue
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if all(part.isidentifier() for part in parts):
                names.append(".".join(parts))
    names.insert(0, "kollabor_cli_main")  # the `kollab` entry point
    return [name for name in names if name not in SKIP]


def reason(exc: BaseException) -> tuple[bool, str]:
    """(is a platform failure, one line saying why)."""
    if isinstance(exc, ModuleNotFoundError):
        top = (exc.name or "").split(".")[0]
        if top in UNIX_ONLY:
            return True, f"imports Unix-only module {top}"
        if top in sys.stdlib_module_names or top.startswith("kollabor") or top == "plugins":
            # A standard or first-party module missing here is a platform failure, not an
            # optional package.
            return True, f"cannot import {exc.name}"
        return False, f"package {top} is not installed"
    frame = traceback.extract_tb(exc.__traceback__)[-1]
    where = f"{Path(frame.filename).name}:{frame.lineno}"
    return True, f"{type(exc).__name__}: {exc} ({where})"


def main() -> int:
    sys.path[:0] = [str(ROOT), *map(str, SRC_DIRS)]
    names = module_names()
    if len(names) < 2:
        print("no kollab modules found")
        return 1
    failed, skipped = [], []
    for name in names:
        try:
            importlib.import_module(name)
        except (Exception, SystemExit) as exc:  # report every module, keep going
            platform, why = reason(exc)
            (failed if platform else skipped).append(f"{name}: {why}")
    for line in skipped:
        print(f"SKIP {line}")
    for line in failed:
        print(f"FAIL {line}")
    imported = len(names) - len(failed) - len(skipped)
    print(
        f"{len(names)} modules: {imported} imported, {len(skipped)} skipped "
        f"(package not installed), {len(failed)} failed"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
