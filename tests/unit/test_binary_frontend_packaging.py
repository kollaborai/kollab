"""The installed app and service must package the same explicitly chosen UI."""

import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import build_binary
from scripts.relay import build_service


@pytest.fixture
def frontend(tmp_path):
    frontend = tmp_path / "chosen-frontend"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "phonelink/loader").mkdir(parents=True)
    (frontend / "phonelink/loader/loader.js").write_bytes(b"/* the only code a directory serves */")
    (frontend / "phonelink/loader/index.html").write_bytes(b"<script src=./loader.js></script>")
    (frontend / "index.html").write_bytes(b'<script src="/assets/app.js"></script>')
    (frontend / "assets/app.js").write_bytes(b"/* chosen frontend */")
    return frontend


def webui_wheel(directory, assets):
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / "kollabor_webui-0.17.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in assets.items():
            archive.writestr("kollabor_webui/static/assistant/" + name, content)
    return wheel


def kollab_wheel(directory):
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / "kollab-0.17.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "kollabor/_build_info.py",
            f"BUILD_BRANCH = 'fixture-branch'\nBUILD_SHA = {'a' * 40!r}\n",
        )
    return wheel


@pytest.mark.parametrize("mismatch", [None, "changed", "missing", "extra"])
def test_binary_wheel_must_match_every_service_frontend_asset(tmp_path, frontend, mismatch):
    archive = tmp_path / "service.tar.gz"
    build_service.build(archive, frontend_dist=frontend)
    prefix = "plugins/hub/phone_static/"
    with tarfile.open(archive) as service:
        assets = {
            member.name.removeprefix(prefix): service.extractfile(member).read()
            for member in service.getmembers()
            if member.name.startswith(prefix)
        }
    # The directory carries only the loader; the binary's wheel carries the whole frontend.
    loader = frontend / "phonelink/loader"
    def files(root):
        return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}

    assert assets == files(loader)
    assets = files(frontend)
    if mismatch == "changed":
        assets["assets/app.js"] = b"/* stale frontend with the same package version */"
    elif mismatch == "missing":
        del assets["assets/app.js"]
    elif mismatch == "extra":
        assets["assets/stale.js"] = b"/* leftover frontend asset */"
    wheel = webui_wheel(tmp_path / "wheels", assets)
    if mismatch is None:
        build_binary.verify_frontend([wheel], frontend)
    else:
        with pytest.raises(SystemExit, match="differs from --frontend-dist: assets/"):
            build_binary.verify_frontend([wheel], frontend)


@pytest.mark.parametrize("input_method", ["argument", "environment"])
def test_binary_build_scopes_the_chosen_frontend_to_its_wheel_build(
    tmp_path, frontend, monkeypatch, input_method
):
    calls = []
    argv = ["build_binary.py"]
    if input_method == "argument":
        argv.extend(["--frontend-dist", str(frontend)])
    else:
        monkeypatch.setenv("KOLLAB_FRONTEND_DIST", str(frontend))
    original = os.environ.get("KOLLAB_FRONTEND_DIST")

    def run(command, **kwargs):
        calls.append((command, kwargs))
        wheel_dir = Path(command[command.index("--out-dir") + 1])
        assets = {
            path.relative_to(frontend).as_posix(): path.read_bytes()
            for path in frontend.rglob("*")
            if path.is_file()
        }
        webui_wheel(wheel_dir, assets)
        kollab_wheel(wheel_dir)

    def build(wheels, out, scratch):
        binary = tmp_path / "kollab-test"
        binary.write_bytes(b"isolated binary fixture")
        binary.with_name(binary.name + ".sha256").write_text("fixture-digest\n")
        return binary

    monkeypatch.setattr(build_binary, "run", run)
    smoke_calls = []

    def smoke(binary, version, build_line):
        smoke_calls.append((binary, version, build_line))

    monkeypatch.setattr(build_binary, "build", build)
    monkeypatch.setattr(build_binary, "smoke", smoke)
    monkeypatch.setattr(sys, "argv", argv)
    assert build_binary.main() == 0
    assert len(calls) == 1
    assert smoke_calls == [
        (tmp_path / "kollab-test", "0.17.0", "Build: fixture-branch @ aaaaaaaa")
    ]
    assert calls[0][1]["env"]["KOLLAB_FRONTEND_DIST"] == str(frontend.absolute())
    assert os.environ.get("KOLLAB_FRONTEND_DIST") == original


@pytest.mark.parametrize("input_method", ["argument", "environment"])
def test_reusing_wheels_rejects_a_different_ui_before_building_the_binary(
    tmp_path, frontend, monkeypatch, input_method
):
    wheels = tmp_path / "wheels"
    webui_wheel(wheels, {"index.html": b"stale UI with the same 0.17.0 package version"})
    kollab_wheel(wheels)

    def unexpected_build(*args, **kwargs):
        pytest.fail("mismatched frontend wheels must be rejected before any build")

    monkeypatch.setattr(build_binary, "run", unexpected_build)
    monkeypatch.setattr(build_binary, "build", unexpected_build)
    argv = ["build_binary.py", "--wheels", str(wheels), "--expect-version", "0.17.0"]
    if input_method == "argument":
        argv.extend(["--frontend-dist", str(frontend)])
    else:
        monkeypatch.setenv("KOLLAB_FRONTEND_DIST", str(frontend))
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="differs from --frontend-dist"):
        build_binary.main()


def test_explicit_frontend_cannot_use_a_symlink_or_a_missing_index(tmp_path, frontend):
    wheel = webui_wheel(tmp_path / "wheels", {})
    link = tmp_path / "frontend-link"
    link.symlink_to(frontend, target_is_directory=True)
    for invalid in (link, tmp_path / "missing"):
        with pytest.raises(SystemExit, match="must contain index.html"):
            build_binary.verify_frontend([wheel], invalid)
        with pytest.raises(ValueError):
            build_service.build(tmp_path / (invalid.name + ".tar.gz"), frontend_dist=invalid)
        assert not (tmp_path / (invalid.name + ".tar.gz")).exists()


def test_service_packages_the_same_environment_selected_frontend(tmp_path, frontend, monkeypatch):
    monkeypatch.setenv("KOLLAB_FRONTEND_DIST", str(frontend))
    archive = tmp_path / "environment-service.tar.gz"
    build_service.build(archive)
    with tarfile.open(archive) as service:
        loader = service.extractfile("plugins/hub/phone_static/loader.js").read()
        assert loader == b"/* the only code a directory serves */"


def test_empty_frontend_environment_cannot_select_a_checkout_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_FRONTEND_DIST", "")
    monkeypatch.setattr(sys, "argv", ["build_binary.py"])
    with pytest.raises(SystemExit):
        build_binary.main()
    archive = tmp_path / "invalid-service.tar.gz"
    with pytest.raises(ValueError, match="KOLLAB_FRONTEND_DIST must name"):
        build_service.build(archive)
    assert not archive.exists()
