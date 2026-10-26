"""Docker consumes a selected asset-only context and stops on failed wheels."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/docker-runtime.sh"
DOCKERFILE = ROOT / "Dockerfile"
STUB = r'''
import hashlib,json,os,pathlib,sys
args=sys.argv[1:]
record={"args":args}
if "--build-context" in args:
    name,context=args[args.index("--build-context")+1].split("=",1)
    folder=pathlib.Path(context)
    record.update(context_name=name,context_path=context,context_files={
        path.relative_to(folder).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
        for path in folder.rglob("*") if path.is_file()
    })
with open(os.environ["DOCKER_TEST_LOG"],"a") as log:
    log.write(json.dumps(record)+"\n")
sys.exit(int(os.environ.get("DOCKER_TEST_EXIT","0")))
'''


@pytest.fixture
def build(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(f"#!{sys.executable}\n" + STUB)
    docker.chmod(0o755)
    context = tmp_path / "source"
    context.mkdir()
    selected = tmp_path / "private rollout/selected"
    (selected / "assets").mkdir(parents=True)
    (selected / "index.html").write_bytes(b"selected application")
    (selected / "assets/app.js").write_bytes(b"\x00selected javascript")
    (selected.parent / "operator-state.json").write_text("must never enter the asset context")
    stale = context / "packages/kollabor-webui/frontend/dist/index.html"
    stale.parent.mkdir(parents=True)
    stale.write_text("old shared frontend")
    env = {
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "HOME": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
        "KOLLAB_DOCKER_IMAGE": "kollab:packaging-test",
        "KOLLAB_DOCKER_CONTEXT": str(context),
        "DOCKER_TEST_LOG": str(tmp_path / "docker.jsonl"),
    }
    return tmp_path, selected, context, env


def run_build(state, **settings):
    directory, _, _, env = state
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), "build"], cwd=directory, env={**env, **settings},
        capture_output=True, text=True, timeout=30,
    )


def calls(state):
    log = state[0] / "docker.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_ordinary_build_keeps_the_source_frontend_stage(build):
    result = run_build(build)
    assert result.returncode == 0, result.stderr
    assert calls(build) == [{"args": ["build", "--target", "runtime", "--tag", "kollab:packaging-test", str(build[2])]}]


@pytest.mark.parametrize("exit_code", [0, 17])
def test_selected_assets_replace_the_stage_without_copying_private_siblings(build, exit_code):
    _, selected, source, _ = build
    expected = {
        "frontend-dist/" + path.relative_to(selected).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in selected.rglob("*") if path.is_file()
    }
    result = run_build(build, KOLLAB_FRONTEND_DIST=str(selected), DOCKER_TEST_EXIT=str(exit_code))
    assert result.returncode == exit_code, result.stderr
    (call,) = calls(build)
    assert call["context_name"] == "frontend"
    assert call["context_files"] == expected
    assert call["args"][-1] == str(source)
    assert not Path(call["context_path"]).exists(), "temporary asset context must be cleaned after either result"
    assert (selected / "index.html").read_bytes() == b"selected application"
    assert (source / "packages/kollabor-webui/frontend/dist/index.html").read_text() == "old shared frontend"


@pytest.mark.parametrize("problem", ["empty", "missing", "no_index", "root_symlink", "asset_symlink", "index_symlink"])
def test_invalid_selected_output_never_reaches_docker(build, problem):
    directory, selected, _, _ = build
    chosen = str(selected)
    if problem == "empty":
        chosen = ""
    elif problem == "missing":
        chosen = str(directory / "missing")
    elif problem == "no_index":
        (selected / "index.html").unlink()
    elif problem == "root_symlink":
        link = directory / "selected-link"
        link.symlink_to(selected, target_is_directory=True)
        chosen = str(link)
    else:
        link = selected / ("index.html" if problem == "index_symlink" else "assets/private.json")
        if link.exists():
            link.unlink()
        link.symlink_to(selected.parent / "operator-state.json")
    result = run_build(build, KOLLAB_FRONTEND_DIST=chosen)
    assert result.returncode != 0
    assert not calls(build)


def test_source_recipe_has_node_and_canonical_changelog_before_selected_wheel_input():
    recipe = DOCKERFILE.read_text()
    frontend, remainder = recipe.split("FROM ${PYTHON_IMAGE} AS wheelhouse", 1)
    wheelhouse, _ = remainder.split("FROM ${PYTHON_IMAGE} AS runtime", 1)
    assert "FROM node:24-slim AS frontend" in frontend
    assert "COPY kollabor/updates/CHANGELOG.md /src/kollabor/updates/CHANGELOG.md" in frontend
    assert "RUN npm run build -- --outDir /frontend-dist" in frontend
    assert "COPY --from=frontend /frontend-dist /opt/kollab-frontend" in wheelhouse
    assert "ENV KOLLAB_FRONTEND_DIST=/opt/kollab-frontend" in wheelhouse


def test_failed_webui_wheel_stops_before_the_root_wheel_can_mask_it(build):
    directory, _, _, env = build
    executable = directory / "bin/python"
    executable.write_text(
        f"#!{sys.executable}\nimport os,sys\n"
        "with open(os.environ['DOCKER_TEST_LOG'],'a') as log: log.write(sys.argv[-1]+'\\n')\n"
        "sys.exit(23 if sys.argv[-1]=='packages/kollabor-webui' else 0)\n"
    )
    executable.chmod(0o755)
    recipe = DOCKERFILE.read_text()
    loop = recipe[recipe.index("for package_dir in"):recipe.index("    && python - <<'PY'")]
    result = subprocess.run(
        ["/bin/sh", "-c", loop.replace("\\\n", "")], cwd=directory, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 23
    assert Path(env["DOCKER_TEST_LOG"]).read_text().splitlines()[-1] == "packages/kollabor-webui"
