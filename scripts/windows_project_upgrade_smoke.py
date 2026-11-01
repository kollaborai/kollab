"""Start Kollab with isolated pre-encoding-change Windows project state, then read it."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath

from windows_halt_smoke import kill_home_processes, smoke_env, start_agent, status_online, wait_until


class ConversationConfig:
    def __init__(self, directory):
        self._conversations_dir = directory

    def get(self, key, default=None):
        return False if key == "kollabor.llm.save_conversations" else default


class Bus:
    def get_service(self, _name):
        return None


def seed_and_read(kollab, home, project, env):
    from kollabor_engine.history_store import read_history_from_disk

    from kollabor.llm.acp_state import ACPBinding, ACPBindingStore
    from kollabor.state.goal_store import GoalStore, open_default_store
    from kollabor_agent.permissions import PermissionManager, RiskAssessor
    from kollabor_ai.conversation_manager import ConversationManager
    from kollabor_config import config_utils, private_fs
    from kollabor_config.loader import ConfigLoader
    from kollabor_config.manager import ConfigManager
    from kollabor_events.permissions_models import RiskAssessmentRules
    from plugins.hub.crystal_store import CrystalStore
    from plugins.hub.local_directory import LocalAgentDirectory
    from plugins.hub.vault import AgentVault, find_active_stream

    # Use the actual old encoder and Windows Path join, not the new resolver.
    legacy_name = str(project.resolve()).replace("/", "_").replace("\\", "_").lstrip("_")
    old = (home / ".kollab" / "projects" / legacy_name).resolve()
    new = home / ".kollab" / "projects" / config_utils.encode_project_path(project)
    assert old != new
    private_fs.ensure_private_dir(old)
    (old / "config.json").write_text(json.dumps({"upgrade_probe": "saved project config"}))
    (old / "permission_approvals.json").write_text(json.dumps({
        "approved_keys": ["saved-key"], "acp_grants": ["acp:saved-grant"]
    }))
    conversation = ConversationManager(ConversationConfig(old / "conversations"))
    conversation.add_message("user", "saved conversation")
    conversation.save_conversation("upgrade_snapshot.json")
    store = GoalStore(old / "goals" / "goals.db")
    goal = store.create("upgrade-conversation", "saved objective", str(project))
    store.close()
    binding = ACPBinding("saved-acp-session", os.path.realpath(project), "test", ("test",), {})
    ACPBindingStore(old / "acp" / "sessions").save("upgrade-conversation", binding)
    identity = old / "hub" / "vaults" / "upgrade"
    nested = identity / "projects" / legacy_name
    nested.mkdir(parents=True)
    CrystalStore(identity).add_entry("**Retained memory** Saved crystal before upgrade")
    stream = nested / "stream.jsonl"
    stream.write_text(json.dumps({"identity": "upgrade", "content": "saved vault", "type": "note"}) + "\n")
    selections = {
        "KOLLAB_PROJECT_DATA_PROJECT": str(project),
        "KOLLAB_PROJECT_DATA_DIR": str(old),
        "KOLLAB_VAULT_DATA_PROJECT": str(project),
        "KOLLAB_VAULT_DATA_IDENTITY": "upgrade",
        "KOLLAB_VAULT_DATA_DIR": str(nested),
    }
    env.update(selections)
    os.environ.update(selections)

    log = home / "upgrade-detached.log"
    launch = start_agent(kollab, project, env, log)
    print(f"detached startup exit: {launch.wait(timeout=90)}")
    wait_until(lambda: status_online(kollab, project, env), 120, "upgrade agent online")

    assert config_utils.get_project_data_dir() == old
    config = ConfigLoader(ConfigManager(home / ".kollab" / "config.json"), fast_mode=True)
    assert config._load_user_config_with_fallback()["upgrade_probe"] == "saved project config"
    restored = ConversationManager(ConversationConfig(config_utils.get_conversations_dir()))
    assert restored.load_conversation(restored.snapshots_dir / "upgrade_snapshot.json")
    assert restored.messages[0]["content"] == "saved conversation"
    assert read_history_from_disk(config_utils.get_conversations_dir(), "upgrade")[0]["content"] == "saved conversation"
    reopened = open_default_store()
    assert reopened.get(goal.goal_id).objective == "saved objective"
    reopened.close()
    permission_config = {"kollabor": {"permissions": {"enabled": True}}}
    permissions = PermissionManager(
        permission_config, RiskAssessor(RiskAssessmentRules(), permission_config), Bus()
    )
    assert permissions._project_approvals == {"saved-key"}
    assert permissions._acp_project_grants == {"acp:saved-grant"}
    binding_store = ACPBindingStore(config_utils.get_project_data_dir() / "acp" / "sessions")
    assert binding_store.load("upgrade-conversation") == binding
    assert LocalAgentDirectory().agents(project), "the upgraded workspace's live Hub presence is missing"
    vault = AgentVault("upgrade")
    assert vault.get_recent_stream()[0]["content"] == "saved vault"
    assert CrystalStore(vault.global_vault_dir).load()[0].summary == "Retained memory"
    assert (identity / "crystallized.md").exists()
    assert find_active_stream(identity) == stream
    assert stream.exists() and not (identity / "stream.jsonl").exists()
    assert not new.exists(), "startup created a parallel project state directory"
    print("upgrade readers: project config, conversation, engine history, goal, permissions, ACP, Hub, vault passed")


def path_cases(home, project):
    from kollabor_config import config_utils as paths

    root = home / "case-config"
    legacy = root / "projects" / paths._legacy_encode_project_path(project)
    current = root / "projects" / paths.encode_project_path(project)
    legacy.mkdir(parents=True)
    (legacy / "config.json").write_text("old")
    os.environ.pop("KOLLAB_PROJECT_DATA_PROJECT")
    os.environ.pop("KOLLAB_PROJECT_DATA_DIR")
    try:
        paths._windows_project_state_dir(project, root)
        raise AssertionError("unmarked state must require a selection")
    except paths.ProjectStateLocationError:
        pass
    current.mkdir()
    (current / "config.json").write_text("new")
    try:
        paths._windows_project_state_dir(project, root)
        raise AssertionError("two existing locations must be ambiguous")
    except paths.ProjectStateLocationError:
        pass
    os.environ["KOLLAB_PROJECT_DATA_PROJECT"] = str(project)
    os.environ["KOLLAB_PROJECT_DATA_DIR"] = str(legacy)
    try:
        assert paths._windows_project_state_dir(project, root) == legacy.resolve()
    finally:
        os.environ["KOLLAB_PROJECT_DATA_PROJECT"] = str(project)
        os.environ["KOLLAB_PROJECT_DATA_DIR"] = str(
            (home / ".kollab" / "projects" / paths._legacy_encode_project_path(project)).resolve()
        )
    assert (legacy / "config.json").read_text() == "old"
    assert (current / "config.json").read_text() == "new"
    # UNC encoding never contained a drive colon. This is a path-semantics check,
    # not a claim that this runner has an accessible SMB share.
    class Fixed:
        def resolve(self):
            return PureWindowsPath(r"\\server\share\work\project")
    unc = Fixed()
    assert paths.encode_project_path(unc) == paths._legacy_encode_project_path(unc)
    print("path cases: same-drive old/new conflict, explicit selection, UNC encoding passed")


def cross_drive_cases(home, other_base):
    from kollabor_config import config_utils as paths

    # Both roots are temporary and created on runner-owned writable volumes.
    with tempfile.TemporaryDirectory(prefix="kollab-cross-", dir=other_base) as temporary:
        base = Path(temporary).resolve()
        project = base / "fresh"
        project.mkdir()
        assert project.drive.lower() != home.resolve().drive.lower(), (project, home)
        root = home / "cross-config"
        current = root / "projects" / paths.encode_project_path(project)
        assert paths._windows_project_state_dir(project, root) == current.resolve(), "fresh cross-drive project blocked"
        project = base / "existing"
        project.mkdir()
        current = root / "projects" / paths.encode_project_path(project)
        tail = paths._legacy_encode_project_path(project)[2:]
        old = project / tail
        old.mkdir()
        (old / "permission_approvals.json").write_text('{"approved_keys":["old"]}')
        try:
            paths._windows_project_state_dir(project, root)
            raise AssertionError("unmarked cross-drive state must require selection")
        except paths.ProjectStateLocationError:
            pass
        saved_project = os.environ["KOLLAB_PROJECT_DATA_PROJECT"]
        saved_dir = os.environ["KOLLAB_PROJECT_DATA_DIR"]
        os.environ["KOLLAB_PROJECT_DATA_PROJECT"] = str(project)
        os.environ["KOLLAB_PROJECT_DATA_DIR"] = str(old)
        assert paths._windows_project_state_dir(project, root) == old.resolve()
        os.environ["KOLLAB_PROJECT_DATA_PROJECT"] = saved_project
        os.environ["KOLLAB_PROJECT_DATA_DIR"] = saved_dir
        current.mkdir(parents=True)
        (current / "config.json").write_text("new")
        try:
            paths._windows_project_state_dir(project, root)
            raise AssertionError("cross-drive candidates must be ambiguous")
        except paths.ProjectStateLocationError:
            pass
        assert (old / "permission_approvals.json").exists() and (current / "config.json").exists()
        print("cross-drive: fresh project usable; existing legacy discovered; both locations preserved")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kollab", required=True)
    args = parser.parse_args()
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("This isolated startup smoke runs only on the Windows CI runner")
    kollab = os.path.abspath(args.kollab)
    # GitHub's Windows runners normally have the profile and runner temp on
    # different volumes. Fail visibly if this runner cannot provide both.
    bases = [
        Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())),
        Path(tempfile.gettempdir()),
        Path(os.environ["USERPROFILE"]) / "AppData" / "Local" / "Temp",
    ]
    bases = [p for p in bases if p.is_dir()]
    pair = next(((a, b) for a in bases for b in bases if a.resolve().drive.lower() != b.resolve().drive.lower()), None)
    if pair is None:
        raise SystemExit("Cross-drive upgrade smoke needs two writable Windows volumes")
    home = Path(tempfile.mkdtemp(prefix="kollab-upgrade-", dir=pair[0]))
    project = home / "workspace"
    project.mkdir()
    env = smoke_env(HOME=str(home), USERPROFILE=str(home))
    env.pop("KOLLAB_PROJECT_DATA_PROJECT", None)
    env.pop("KOLLAB_PROJECT_DATA_DIR", None)
    env.pop("KOLLAB_VAULT_DATA_PROJECT", None)
    env.pop("KOLLAB_VAULT_DATA_IDENTITY", None)
    env.pop("KOLLAB_VAULT_DATA_DIR", None)
    os.environ.clear()
    os.environ.update(env)
    (home / ".kollab").mkdir(mode=0o700)
    (home / ".kollab" / "config.json").write_text(json.dumps({"application": {"setup_completed": True}}))
    original = Path.cwd()
    os.chdir(project)
    try:
        seed_and_read(kollab, home, project, env)
        path_cases(home, project)
        cross_drive_cases(home, pair[1])
    finally:
        try:
            subprocess.run([kollab, "--hub", "stop", "all"], cwd=project, env=env, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
        kill_home_processes(home)
        os.chdir(original)
        shutil.rmtree(home, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
