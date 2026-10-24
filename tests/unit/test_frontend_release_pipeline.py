"""Every release consumer must use the one selected compiled frontend artifact."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())["jobs"]


def artifact_steps(job, action):
    return [step for step in job["steps"] if step.get("uses", "").startswith(f"actions/{action}-artifact@")]


def test_binary_service_and_pypi_consume_one_frontend_artifact():
    binaries = workflow("binaries.yml")
    publish = workflow("publish.yml")
    frontend = binaries["frontend"]
    uploads = {step["with"]["name"]: step["with"] for step in artifact_steps(frontend, "upload")}
    selected = uploads["frontend-dist"]["path"]
    assert selected == "${{ runner.temp }}/kollab-frontend"
    compile_steps = [step["run"] for step in frontend["steps"] if "npm run build" in step.get("run", "")]
    assert len(compile_steps) == 1
    assert '--outDir "$RUNNER_TEMP/kollab-frontend"' in compile_steps[0]
    service = next(step["run"] for step in frontend["steps"] if "scripts/relay/build_service.py" in step.get("run", ""))
    assert '--frontend-dist "$RUNNER_TEMP/kollab-frontend"' in service
    assert "connection-service" in uploads
    assert binaries["build"]["needs"] == "frontend"
    downloads = artifact_steps(binaries["build"], "download")
    assert any(step["with"] == {"name": "frontend-dist", "path": selected} for step in downloads)
    binary = next(
        step["run"] for step in binaries["build"]["steps"] if "scripts/build_binary.py" in step.get("run", "")
    )
    assert '--frontend-dist "$RUNNER_TEMP/kollab-frontend"' in binary
    assert publish["publish"]["needs"] == "binaries"
    assert any(
        step["with"] == {"name": "frontend-dist", "path": selected}
        for step in artifact_steps(publish["publish"], "download")
    )
    package = next(step for step in publish["publish"]["steps"] if "--package \"$pkg\"" in step.get("run", ""))
    assert package["env"]["KOLLAB_FRONTEND_DIST"] == selected


def test_internal_frontend_and_service_artifacts_stay_out_of_public_release_payloads():
    binaries = workflow("binaries.yml")
    publish = workflow("publish.yml")
    for name in ("frontend-dist", "connection-service"):
        assert not name.startswith("kollab-")
    for job in (binaries["release-manifest"], publish["release"]):
        downloads = artifact_steps(job, "download")
        assert all(step["with"].get("pattern") == "kollab-*" for step in downloads)
    assert binaries["desktop"]["needs"] == "build"
    assert binaries["desktop-linux"]["needs"] == "build"


def test_ci_build_jobs_have_the_supported_frontend_toolchain():
    jobs = workflow("ci.yml")
    for name in ("tests", "standards-check", "binary-smoke"):
        steps = jobs[name]["steps"]
        node = next(i for i, step in enumerate(steps) if step.get("uses", "").startswith("actions/setup-node@"))
        build = next(
            i for i, step in enumerate(steps)
            if "uv sync" in step.get("run", "") or "scripts/build_binary.py" in step.get("run", "")
        )
        assert node < build
        assert steps[node]["with"]["node-version"] == "24"
