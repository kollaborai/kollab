"""The voice service runs in its own Python 3.12 runtime, which has no host packages."""

import ast
from pathlib import Path

import kollabor_voice

HOST_PACKAGES = {"kollabor", "kollabor_ai", "kollabor_agent", "kollabor_engine"}
# Host-side modules the service never imports in its isolated runtime.
HOST_ONLY = {"responder.py"}


def test_service_modules_import_no_host_package_at_any_depth():
    root = Path(kollabor_voice.__file__).parent
    offenders = []
    for path in sorted(root.glob("*.py")):
        if path.name in HOST_ONLY:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom) and not node.level
                else []
            )
            offenders += [
                f"{path.name}:{node.lineno} imports {name}"
                for name in names
                if name.split(".")[0] in HOST_PACKAGES
            ]
    assert not offenders, offenders
