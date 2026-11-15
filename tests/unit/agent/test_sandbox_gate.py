"""Promotion gate (#725): shared checkouts are touched only after scratch validation.

These drive the real ToolExecutor, so the gate is proven at the tool layer,
not in a prompt.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.sandbox_gate import SandboxGate
from kollabor_agent.tool_executor import ToolExecutor

pytestmark = pytest.mark.asyncio

PASSING = f'{sys.executable} -c "import sys; sys.exit(0)"'
FAILING = f'{sys.executable} -c "import sys; sys.exit(3)"'


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def git(cwd, *args):
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "PATH": __import__("os").environ["PATH"],
        "HOME": str(cwd),
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(path):
    path.mkdir(parents=True)
    git(path, "init", "-q")
    (path / "a.txt").write_text("a\n")
    git(path, "add", "a.txt")
    git(path, "commit", "-qm", "init")
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.delenv("KOLLAB_SANDBOX_GATE_OVERRIDE", raising=False)
    monkeypatch.setenv("KOLLAB_IDENTITY", "ruby")
    shared = make_repo(tmp_path / "shared")
    scratch = make_repo(tmp_path / "scratch")
    audit = tmp_path / "audit.jsonl"
    values = {
        "sandbox_gate.enabled": True,
        "sandbox_gate.protected_roots": [str(shared)],
        "sandbox_gate.validation": [PASSING],
        "sandbox_gate.scratch_root": str(tmp_path),
        "sandbox_gate.audit_log": str(audit),
    }

    def build(workspace=shared, **overrides):
        values.update(overrides)
        bus = MagicMock()
        bus.emit_with_hooks = AsyncMock(return_value={})
        return ToolExecutor(MagicMock(), bus, config=FakeConfig(values), workspace=workspace)

    class Env:
        pass

    e = Env()
    e.shared, e.scratch, e.audit, e.values, e.build = shared, scratch, audit, values, build
    return e


def create(path, content="x"):
    return {"id": "t1", "type": "file_create", "file": str(path), "content": content}


def records(audit):
    if not audit.exists():
        return []
    return [json.loads(line) for line in audit.read_text().splitlines()]


async def promote(executor, scratch, root):
    return await executor.execute_tool(
        {"id": "p1", "type": "sandbox_promote", "scratch": str(scratch), "root": str(root)}
    )


async def test_write_into_protected_root_is_refused_and_logged(env):
    executor = env.build()
    target = env.shared / "new.txt"
    result = await executor.execute_tool(create(target))
    assert not result.success
    assert "sandbox" in result.error.lower()
    assert not target.exists()
    refused = [r for r in records(env.audit) if r["event"] == "refused"]
    assert refused and refused[0]["agent"] == "ruby"
    assert refused[0]["root"] == str(env.shared.resolve())


async def test_case_variant_of_protected_root_is_refused_on_case_insensitive_fs(env):
    root = Path.cwd().resolve()
    variant = Path(str(root).replace("/Users/", "/USERS/", 1))
    if variant == root or not variant.exists() or not variant.samefile(root):
        pytest.skip("filesystem is case-sensitive")

    gate = SandboxGate()
    touched = gate._roots_touched(
        {"type": "file_create", "file": str(variant / "case-variant.txt")},
        root,
        [root],
    )

    assert touched == [root]


async def test_gate_is_off_by_default(env):
    executor = env.build(**{"sandbox_gate.enabled": False})
    target = env.shared / "new.txt"
    assert (await executor.execute_tool(create(target))).success
    assert target.exists()


async def test_write_outside_protected_roots_is_allowed(env):
    executor = env.build(workspace=env.scratch)
    target = env.scratch / "new.txt"
    assert (await executor.execute_tool(create(target))).success


async def test_relative_path_in_protected_workspace_is_refused(env):
    executor = env.build()
    result = await executor.execute_tool(create("rel.txt"))
    assert not result.success
    assert not (env.shared / "rel.txt").exists()


async def test_read_allowed_only_when_configured(env):
    read = {"id": "r1", "type": "file_read", "file": str(env.shared / "a.txt")}
    allowed = env.build(**{"sandbox_gate.allow_read": True})
    assert (await allowed.execute_tool(read)).success
    denied = env.build(**{"sandbox_gate.allow_read": False})
    result = await denied.execute_tool(read)
    assert not result.success and "sandbox" in result.error.lower()


async def test_terminal_in_or_naming_protected_root_is_refused(env, tmp_path):
    executor = env.build(workspace=env.scratch)
    marker = env.shared / "touched"
    by_cwd = await executor.execute_tool(
        {"id": "c1", "type": "terminal", "command": "touch touched", "cwd": str(env.shared)}
    )
    by_path = await executor.execute_tool(
        {"id": "c2", "type": "terminal", "command": f"touch {marker}"}
    )
    assert not by_cwd.success and not by_path.success
    assert not marker.exists()
    ok = await executor.execute_tool(
        {"id": "c3", "type": "terminal", "command": "touch fine", "cwd": str(env.scratch)}
    )
    assert ok.success and (env.scratch / "fine").exists()


async def test_promotion_allows_one_write_then_token_is_spent(env):
    executor = env.build()
    promoted = await promote(executor, env.scratch, env.shared)
    assert promoted.success, promoted.error
    first = await executor.execute_tool(create(env.shared / "one.txt"))
    second = await executor.execute_tool(create(env.shared / "two.txt"))
    assert first.success
    assert not second.success and not (env.shared / "two.txt").exists()
    entry = [r for r in records(env.audit) if r["event"] == "promoted"][0]
    assert entry["agent"] == "ruby"
    assert entry["sha"] == git(env.scratch, "rev-parse", "HEAD")
    assert entry["validation"][0]["exit_code"] == 0
    assert entry["timestamp"]


async def test_failing_validation_issues_no_token(env):
    executor = env.build(**{"sandbox_gate.validation": [PASSING, FAILING]})
    result = await promote(executor, env.scratch, env.shared)
    assert not result.success
    assert not (await executor.execute_tool(create(env.shared / "x.txt"))).success
    assert any(r["event"] == "promotion_rejected" for r in records(env.audit))


async def test_no_validation_commands_fails_closed(env):
    executor = env.build(**{"sandbox_gate.validation": []})
    assert not (await promote(executor, env.scratch, env.shared)).success


async def test_dirty_scratch_is_not_promoted(env):
    (env.scratch / "stray.txt").write_text("unintended")
    executor = env.build()
    result = await promote(executor, env.scratch, env.shared)
    assert not result.success and "status" in result.error.lower()


async def test_new_sha_voids_the_token(env):
    executor = env.build()
    assert (await promote(executor, env.scratch, env.shared)).success
    (env.scratch / "b.txt").write_text("b\n")
    git(env.scratch, "add", "b.txt")
    git(env.scratch, "commit", "-qm", "later")
    result = await executor.execute_tool(create(env.shared / "x.txt"))
    assert not result.success and not (env.shared / "x.txt").exists()


async def test_token_expires(env):
    executor = env.build()
    assert (await promote(executor, env.scratch, env.shared)).success
    gate = executor.sandbox_gate
    real = gate.clock
    gate.clock = lambda: real() + 15 * 60 + 1
    assert not (await executor.execute_tool(create(env.shared / "x.txt"))).success


async def test_token_is_scoped_to_one_root(env, tmp_path):
    other = make_repo(tmp_path / "other")
    executor = env.build(
        **{"sandbox_gate.protected_roots": [str(env.shared), str(other)]}
    )
    assert (await promote(executor, env.scratch, env.shared)).success
    elsewhere = env.build(workspace=other)
    elsewhere.sandbox_gate = executor.sandbox_gate
    assert not (await elsewhere.execute_tool(create(other / "x.txt"))).success


async def test_promoting_into_an_unprotected_root_is_refused(env, tmp_path):
    executor = env.build()
    assert not (await promote(executor, env.scratch, tmp_path)).success


async def test_scratch_inside_protected_root_cannot_promote(env):
    nested = make_repo(env.shared / "nested")
    executor = env.build()
    assert not (await promote(executor, nested, env.shared)).success


async def test_owner_override_is_env_only_timeboxed_and_logged(env, monkeypatch):
    executor = env.build()
    gate = executor.sandbox_gate
    now = gate.clock()
    monkeypatch.setenv("KOLLAB_SANDBOX_GATE_OVERRIDE", f"{env.shared}@{now + 600}")
    assert (await executor.execute_tool(create(env.shared / "o.txt"))).success
    assert any(r["event"] == "override" for r in records(env.audit))
    monkeypatch.setenv("KOLLAB_SANDBOX_GATE_OVERRIDE", f"{env.shared}@{now - 1}")
    assert not (await executor.execute_tool(create(env.shared / "p.txt"))).success


async def test_config_cannot_grant_an_override(env):
    executor = env.build(**{"sandbox_gate.override": str(env.shared)})
    assert not (await executor.execute_tool(create(env.shared / "x.txt"))).success


async def test_override_must_be_timeboxed_and_names_one_root(env, monkeypatch, tmp_path):
    other = make_repo(tmp_path / "other")
    executor = env.build(**{"sandbox_gate.protected_roots": [str(env.shared), str(other)]})
    now = executor.sandbox_gate.clock()
    monkeypatch.setenv("KOLLAB_SANDBOX_GATE_OVERRIDE", f"{env.shared}@{now + 7200}")
    assert not (await executor.execute_tool(create(env.shared / "x.txt"))).success
    monkeypatch.setenv("KOLLAB_SANDBOX_GATE_OVERRIDE", f"{env.shared}@{now + 600}")
    assert (await executor.execute_tool(create(env.shared / "y.txt"))).success
    elsewhere = env.build(workspace=other)
    assert not (await elsewhere.execute_tool(create(other / "z.txt"))).success
