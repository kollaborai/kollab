"""Every supported headless role must work without editable workspace packages."""

import ast
import os
import subprocess
import sys
import sysconfig
import tarfile

import pytest

from scripts.relay import build_service


@pytest.fixture
def artifact_tree(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<main>PhoneLink</main>")
    archive = tmp_path / "source.tar.gz"
    build_service.build(archive, frontend_dist=frontend)
    tree = tmp_path / "tree"
    with tarfile.open(archive) as tar:
        tar.extractall(tree, filter="data")
    return tree


def child_env(tree):
    home = tree.parent / "home"
    home.mkdir(exist_ok=True)
    return {
        "PATH": os.defpath,
        "HOME": str(home),
        "TMPDIR": str(tree.parent),
        "LANG": "C",
        "KOLLAB_NO_KEYRING": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1",
    }


def test_packaged_relay_imports_and_help_from_allowlist_alone(artifact_tree):
    assert (artifact_tree / "plugins/hub/phone_static/index.html").read_text() == "<main>PhoneLink</main>"
    modules = [
        name[:-3].replace("/", ".").removesuffix(".__init__")
        for name in build_service.FILES if name.endswith(".py")
    ]
    script = """
import contextlib
import importlib
import importlib.abc
import io
import json
import pathlib
import runpy
import socket
import sys
import tomllib

tree = pathlib.Path.cwd()
sys.path.extend([str(tree), SITE_PACKAGES])

class ArtifactOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "kollabor" or fullname.startswith(("kollabor.", "kollabor_", "plugins.")):
            candidate = tree.joinpath(*fullname.split("."))
            if not candidate.is_dir() and not candidate.with_suffix(".py").is_file():
                raise ModuleNotFoundError(fullname, name=fullname)

sys.meta_path.insert(0, ArtifactOnly())
def forbidden_network(*args, **kwargs):
    raise AssertionError("network is forbidden in artifact checks")
socket.socket.connect = forbidden_network
socket.socket.connect_ex = forbidden_network
socket.socket.sendto = forbidden_network
for module in MODULES:
    importlib.import_module(module)
from kollabor import __version__
assert __version__ == tomllib.loads((tree / "pyproject.toml").read_text())["project"]["version"]

def help_for(path, arguments, module=False):
    sys.argv = [path, *arguments]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        try:
            if module:
                sys.modules.pop(path, None)
                runpy.run_module(path, run_name="__main__")
            else:
                runpy.run_path(str(tree / path), run_name="__main__")
        except SystemExit as exc:
            assert exc.code == 0
    assert "usage:" in output.getvalue()

help_for("kollabor_cli_main.py", ["relay", "run", "--help"])
help_for("kollabor_cli_main.py", ["relay", "serve", "--origin", "https://relay.example", "--help"])
help_for("kollabor_cli_main.py", ["relay", "serve", "--help"])
help_for("plugins.hub.dns.discovery_publish", ["--help"], module=True)
help_for("scripts/relay/peer_probe.py", ["--help"])
help_for("scripts/relay/measure_capacity.py", ["--help"])

managed = pathlib.Path.home() / ".kollab/network/member"
sys.argv = ["peer_probe.py", "--workspace", str(tree / "workspace"), "--state-dir", str(managed)]
errors = io.StringIO()
with contextlib.redirect_stderr(errors):
    try:
        runpy.run_path(str(tree / "scripts/relay/peer_probe.py"), run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 1
assert json.loads(errors.getvalue()) == {"error": "managed_state"}
assert not managed.exists()
assert not any(name.startswith(("kollabor_ai", "kollabor_config", "kollabor_agent", "kollabor_events"))
               for name in sys.modules)
""".replace("SITE_PACKAGES", repr(sysconfig.get_path("purelib"))).replace("MODULES", repr(modules))
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script],
        cwd=artifact_tree, capture_output=True, text=True, env=child_env(artifact_tree),
    )
    assert result.returncode == 0, result.stderr[-3000:]


def test_headless_sources_do_not_import_the_app_controller(artifact_tree):
    assert "plugins/hub/relay_commands.py" not in build_service.FILES
    for name in build_service.FILES:
        if not name.endswith(".py"):
            continue
        imports = [
            node.module for node in ast.walk(ast.parse((artifact_tree / name).read_text()))
            if isinstance(node, ast.ImportFrom) and node.module
        ]
        imports.extend(
            alias.name for node in ast.walk(ast.parse((artifact_tree / name).read_text()))
            if isinstance(node, ast.Import) for alias in node.names
        )
        assert not any(module.endswith("relay_commands") for module in imports), name


def test_extracted_project_uses_the_deploy_venv_and_pip_without_uv(artifact_tree):
    # The root pyproject carries version data, not a workspace to uv sync here.
    env = child_env(artifact_tree)
    result = subprocess.run(
        [sys.executable, "-I", "-m", "venv", ".venv"],
        cwd=artifact_tree, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr[-2000:]

    # Run pip's actual install command shape as well. With no index or cached
    # wheels, its expected failure is dependency resolution, not uv workspace
    # discovery or a build of the copied pyproject.
    pip = artifact_tree / ".venv" / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
    result = subprocess.run(
        [str(pip), "install", "--dry-run", "--no-index", "--no-deps", "-r", "scripts/relay/requirements.txt"],
        cwd=artifact_tree, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "No matching distribution found for" in result.stderr
    assert "pyproject" not in result.stderr and "packages/" not in result.stderr
    python = artifact_tree / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = subprocess.run(
        [str(python), "-I", "-c", "import pip, sys; print(sys.prefix); print(pip.__file__)"],
        cwd=artifact_tree, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert all(str(artifact_tree / ".venv") in line for line in result.stdout.splitlines())

    # Parse the real hashed requirements offline through this venv's pip.
    # Fetching/installing the pins belongs to rollout; do not contact an index.
    result = subprocess.run(
        [
            str(python), "-I", "-c",
            "from pip._internal.network.session import PipSession; "
            "from pip._internal.req.req_file import parse_requirements; "
            "items = list(parse_requirements('scripts/relay/requirements.txt', session=PipSession())); "
            "assert items and all('==' in item.requirement and item.options.get('hashes') for item in items)",
        ],
        cwd=artifact_tree, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_phone_assets_must_exist_before_an_artifact_is_created(tmp_path):
    archive = tmp_path / "missing.tar.gz"
    with pytest.raises(ValueError, match="Build the Kollab frontend"):
        build_service.build(archive, frontend_dist=tmp_path / "missing")
    assert not archive.exists()
