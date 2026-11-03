"""Protect the separation between source builds and release write authority."""

import re
from pathlib import Path

import yaml

from scripts.publish import PACKAGES

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def test_only_manual_main_controller_can_start_publishing():
    doc = workflow("publish.yml")
    assert set(doc.get("on", doc.get(True))) == {"workflow_dispatch"}
    assert doc["jobs"]["validate"]["if"] == "github.ref == 'refs/heads/main'"
    assert doc["permissions"] == {"contents": "read"}
    assert doc["concurrency"]["cancel-in-progress"] is False


def test_write_authority_is_scoped_to_publishers():
    jobs = workflow("publish.yml")["jobs"]
    for name, job in jobs.items():
        permissions = job.get("permissions", {})
        if name == "publish-packages":
            assert permissions == {"contents": "read", "id-token": "write"}
            assert job["environment"]["name"] == "pypi-publish"
        elif name == "release":
            assert permissions == {"contents": "write"}
            assert job["environment"] == "github-release"
        else:
            assert not any(value == "write" for value in permissions.values())
    text = (ROOT / ".github/workflows/publish.yml").read_text()
    assert "secrets." not in text
    assert "skip-existing" not in text
    assert "--clobber" not in text
    assert "git push" not in text
    assert "HOMEBREW_TAP_TOKEN" not in text


def test_publisher_runs_controller_code_and_does_not_build_packages():
    jobs = workflow("publish.yml")["jobs"]
    for name in ("publish-packages", "release"):
        job = jobs[name]
        checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"]["ref"] == "${{ needs.validate.outputs.controller_sha }}"
        assert checkout["with"]["persist-credentials"] is False
        commands = "\n".join(s.get("run", "") for s in job["steps"])
        assert "uv build" not in commands
        assert "pip install" not in commands
        assert "npm " not in commands


def test_all_builds_use_selected_immutable_source_with_read_only_token():
    publish = workflow("publish.yml")["jobs"]
    assert publish["binaries"]["with"]["source_ref"] == "${{ needs.validate.outputs.source_sha }}"
    assert publish["binaries"]["uses"] == "./.github/workflows/binaries.yml"
    build = publish["build-packages"]
    checkout = next(s for s in build["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ needs.validate.outputs.source_sha }}"
    commands = "\n".join(s.get("run", "") for s in build["steps"])
    for package in PACKAGES:
        assert package in commands
    assert "kollabor-voice" not in commands
    binaries = workflow("binaries.yml")
    assert binaries["permissions"] == {"contents": "read"}
    for job in binaries["jobs"].values():
        assert not any(v == "write" for v in job.get("permissions", {}).values())
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"] == {"ref": "${{ inputs.source_ref || github.sha }}", "persist-credentials": False}


def test_actions_are_immutable_and_root_upload_follows_dependency_verification():
    for name in ("publish.yml", "binaries.yml"):
        for job in workflow(name)["jobs"].values():
            for step in job.get("steps", []):
                if "uses" in step:
                    assert re.fullmatch(r"[^@]+@[a-f0-9]{40}", step["uses"])
    steps = workflow("publish.yml")["jobs"]["publish-packages"]["steps"]
    workspace = next(i for i, s in enumerate(steps) if s.get("with", {}).get("packages-dir") == "upload/workspace/")
    verify = next(i for i, s in enumerate(steps) if "--workspace-only" in s.get("run", ""))
    root = next(i for i, s in enumerate(steps) if s.get("with", {}).get("packages-dir") == "upload/root/")
    assert workspace < verify < root
    assert "publish-packages" in workflow("publish.yml")["jobs"]["release"]["needs"]
