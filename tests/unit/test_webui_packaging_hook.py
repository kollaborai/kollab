"""A wheel must consume selected fresh assets or fail without touching source dist."""

import importlib.util
import shutil
import subprocess
import sys
import tarfile
import types
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def hook(tmp_path, monkeypatch):
    # Hatch is a build-system dependency, not a runtime/test dependency. These
    # hook tests need only its read-only root/target interface; real wheels are
    # also checked through uv build during packaging acceptance.
    interface = types.ModuleType("hatchling.builders.hooks.plugin.interface")
    interface.BuildHookInterface = type("BuildHookInterface", (), {})
    monkeypatch.setitem(sys.modules, interface.__name__, interface)
    spec = importlib.util.spec_from_file_location(
        "webui_packaging_hook", ROOT / "packages/kollabor-webui/hatch_build.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance = module.CustomBuildHook()
    package_root = tmp_path / "repo/packages/kollabor-webui"
    instance.root = str(package_root)
    instance.target_name = "wheel"
    monkeypatch.delenv("KOLLAB_FRONTEND_DIST", raising=False)
    frontend = package_root / "frontend"
    (frontend / "dist").mkdir(parents=True)
    (frontend / "dist/index.html").write_text("old source dist")
    (frontend / "package.json").write_text("{}")
    (frontend / "package-lock.json").write_text("{}")
    return instance, module, frontend


def test_failed_compilation_cannot_package_the_old_dist(hook, monkeypatch):
    instance, module, frontend = hook
    monkeypatch.setattr(module.shutil, "which", lambda name: "/test/npm")

    def run(command, cwd):
        if "--outDir" in command:
            output = Path(command[command.index("--outDir") + 1])
            output.mkdir()
            (output / "index.html").write_text("partial failed output")
            raise RuntimeError("injected compilation failure")

    monkeypatch.setattr(instance, "_run", run)
    build_data = {}
    with pytest.raises(RuntimeError, match="injected compilation failure"):
        instance.initialize("standard", build_data)
    assert (frontend / "dist/index.html").read_text() == "old source dist"
    assert not build_data.get("force_include")
    assert instance._frontend_output is None


def test_missing_npm_cannot_package_the_old_dist(hook, monkeypatch):
    instance, module, frontend = hook
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="npm is required"):
        instance.initialize("standard", {})
    assert (frontend / "dist/index.html").read_text() == "old source dist"


def test_isolated_output_survives_until_wheel_consumption(hook, tmp_path, monkeypatch):
    instance, module, frontend = hook
    monkeypatch.setattr(module.shutil, "which", lambda name: "/test/npm")

    def run(command, cwd):
        if "--outDir" in command:
            output = Path(command[command.index("--outDir") + 1])
            output.mkdir()
            (output / "index.html").write_bytes(b"fresh isolated app")

    monkeypatch.setattr(instance, "_run", run)
    build_data = {}
    instance.initialize("standard", build_data)
    (selected,) = build_data["force_include"]
    selected = Path(selected)
    assert selected != frontend / "dist"
    wheel = tmp_path / "test.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.write(selected / "index.html", "kollabor_webui/static/assistant/index.html")
    instance.finalize("standard", build_data, str(wheel))
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("kollabor_webui/static/assistant/index.html") == b"fresh isolated app"
    assert not selected.exists()
    assert (frontend / "dist/index.html").read_text() == "old source dist"


def test_explicit_frontend_is_reused_without_npm_and_survives_finalize(hook, tmp_path, monkeypatch):
    instance, module, frontend = hook
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "index.html").write_text("chosen app")
    monkeypatch.setenv("KOLLAB_FRONTEND_DIST", str(selected))
    monkeypatch.setattr(module.shutil, "which", lambda name: pytest.fail("explicit frontend must not invoke npm"))
    build_data = {}
    instance.initialize("standard", build_data)
    assert build_data["force_include"] == {str(selected): "kollabor_webui/static/assistant"}
    instance.finalize("standard", build_data, "wheel.whl")
    assert (selected / "index.html").read_text() == "chosen app"
    assert (frontend / "dist/index.html").read_text() == "old source dist"


def test_invalid_explicit_input_does_not_fall_back_to_checkout_dist(hook, tmp_path, monkeypatch):
    instance, _, frontend = hook
    monkeypatch.setenv("KOLLAB_FRONTEND_DIST", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="directory containing index.html"):
        instance.initialize("standard", {})
    assert (frontend / "dist/index.html").read_text() == "old source dist"


def test_source_distribution_carries_canonical_changelog_without_compiling(hook, monkeypatch):
    instance, module, frontend = hook
    instance.target_name = "sdist"
    canonical = Path(instance.root).parents[1] / "kollabor/updates/CHANGELOG.md"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("canonical release notes")
    (frontend / "CHANGELOG.md").write_text("stale local notes must not replace canonical input")
    monkeypatch.setattr(module.shutil, "which", lambda name: pytest.fail("sdist must not invoke npm"))
    build_data = {}
    instance.initialize("standard", build_data)
    assert build_data["force_include"] == {str(canonical): "frontend/CHANGELOG.md"}


def test_standalone_source_distribution_requires_its_packaged_changelog(hook):
    instance, _, frontend = hook
    instance.target_name = "sdist"
    with pytest.raises(RuntimeError, match="canonical frontend changelog input is missing"):
        instance.initialize("standard", {})
    (frontend / "CHANGELOG.md").write_text("packaged canonical notes")
    instance.initialize("standard", {})


def test_real_source_archive_excludes_frontend_build_metadata(tmp_path):
    """The actual sdist rules must keep local compiler state out of uploads."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for the source-archive build check")
    source = ROOT / "packages/kollabor-webui"
    package = tmp_path / "kollabor-webui"
    package.mkdir()
    for name in ("pyproject.toml", "hatch_build.py", "README.md"):
        shutil.copy2(source / name, package / name)
    module = package / "src/kollabor_webui"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text("")
    frontend = package / "frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "src/main.ts").write_text("export const source = true;\n")
    (frontend / "CHANGELOG.md").write_text("packaged release notes")
    generated = (
        frontend / "tsconfig.tsbuildinfo",
        frontend / "cache/compiler.tsbuildinfo",
        frontend / "node_modules/local-package/index.js",
        frontend / "dist/index.html",
    )
    for path in generated:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("local generated state")
    output = tmp_path / "artifacts"
    result = subprocess.run(
        [uv, "build", "--sdist", "--no-sources", "--out-dir", str(output), str(package)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    (archive_path,) = output.glob("*.tar.gz")
    with tarfile.open(archive_path) as archive:
        names = archive.getnames()
        assert any(name.endswith("/frontend/src/main.ts") for name in names)
        assert any(name.endswith("/frontend/CHANGELOG.md") for name in names)
        assert not any(name.endswith(".tsbuildinfo") for name in names)
        assert not any("/frontend/node_modules/" in name for name in names)
        assert not any("/frontend/dist/" in name for name in names)
    assert all(path.read_text() == "local generated state" for path in generated)
