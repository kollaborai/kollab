"""Generated security checks for process enumeration boundaries."""

import ast
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import psutil

from kollabor.llm.acp_bridge import ACPRunner
from plugins.hub import network_halt as nh

REPO_ROOT = Path(__file__).resolve().parents[2]
ENUMERATION_METHODS = {"process_iter", "pids", "environ", "children"}
REQUIRED_METHODS = {"process_iter", "pids", "environ", "children"}
COVERED_METHODS = {
    "process_iter": "test_process_iter_generated_pid_reuse_and_owner_filtering",
    "pids": "test_pid_and_environment_generated_rows_do_not_expand_sweep",
    "environ": "test_pid_and_environment_generated_rows_do_not_expand_sweep",
    "children": "test_recursive_children_generated_trees_survive_races_safely",
}


def _enumeration_methods() -> set[str]:
    """Find psutil enumeration calls in production Python source."""
    methods: set[str] = set()
    for source_root in (REPO_ROOT / "kollabor", REPO_ROOT / "plugins", REPO_ROOT / "packages"):
        for path in source_root.rglob("*.py"):
            if any(part in {"node_modules", ".venv", "__pycache__"} for part in path.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            imports_psutil = any(
                (isinstance(node, ast.Import) and any(alias.name == "psutil" for alias in node.names))
                or (isinstance(node, ast.ImportFrom) and node.module == "psutil")
                for node in ast.walk(tree)
            )
            direct_imports = {
                alias.asname or alias.name: alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == "psutil"
                for alias in node.names
            }
            if not imports_psutil:
                continue
            aliases = {name: {method} for name, method in direct_imports.items()}
            assignments = [
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.Assign, ast.AnnAssign))
            ]
            changed = True
            while changed:
                changed = False
                for node in assignments:
                    value = node.value
                    if isinstance(node, ast.Assign):
                        targets = node.targets
                    else:
                        targets = [node.target]
                    methods = (
                        {value.attr}
                        if isinstance(value, ast.Attribute)
                        else aliases.get(value.id, set())
                        if isinstance(value, ast.Name)
                        else set()
                    )
                    if not methods:
                        continue
                    for target in targets:
                        if isinstance(target, ast.Name):
                            target_methods = aliases.setdefault(target.id, set())
                            new_methods = methods - target_methods
                            if new_methods:
                                target_methods.update(new_methods)
                                changed = True
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute):
                        called_methods = {node.func.attr}
                    elif isinstance(node.func, ast.Name):
                        called_methods = aliases.get(node.func.id, set())
                    else:
                        continue
                    for method in called_methods:
                        if method in ENUMERATION_METHODS:
                            methods.add(method)
                        elif method.startswith("process_") or method.endswith("_pids"):
                            methods.add(method)
    return methods


def test_every_process_enumerator_has_property_fuzz_coverage() -> None:
    """Fail when production starts using a process-enumeration path without a fuzz test."""
    missing_methods = REQUIRED_METHODS - COVERED_METHODS.keys()
    uncovered_methods = _enumeration_methods() - COVERED_METHODS.keys()
    missing_tests = set(COVERED_METHODS.values()) - globals().keys()
    assert not missing_methods and not uncovered_methods and not missing_tests, (
        f"missing fuzz methods={sorted(missing_methods)} "
        f"uncovered source methods={sorted(uncovered_methods)} "
        f"missing test functions={sorted(missing_tests)}"
    )


def test_process_enumerator_guard_detects_direct_psutil_imports(tmp_path, monkeypatch) -> None:
    """A direct import still counts as an enumerator needing property coverage."""
    source_root = tmp_path / "kollabor"
    source_root.mkdir()
    (tmp_path / "plugins").mkdir()
    (tmp_path / "packages").mkdir()
    (source_root / "direct_import.py").write_text(
        "from psutil import process_iter\nrows = process_iter()\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)

    assert "process_iter" in _enumeration_methods()


def test_process_enumerator_guard_detects_local_method_aliases(tmp_path, monkeypatch) -> None:
    """Aliasing an enumerator must not hide it from the coverage guard."""
    source_root = tmp_path / "kollabor"
    source_root.mkdir()
    (tmp_path / "plugins").mkdir()
    (tmp_path / "packages").mkdir()
    (source_root / "aliased_enumerators.py").write_text(
        "import psutil\n"
        "all_processes = psutil.pids\n"
        "all_processes()\n"
        "read_environment = psutil.Process(1).environ\n"
        "read_environment()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)

    assert {"pids", "environ"} <= _enumeration_methods()


class _ProcessRow:
    def __init__(self, pid: int, ppid: int, started: float, username: str | None):
        self.info = {
            "pid": pid,
            "ppid": ppid,
            "create_time": started,
            "name": "worker",
            "username": username,
        }


def test_process_iter_generated_pid_reuse_and_owner_filtering(monkeypatch) -> None:
    """Generated Windows process tables keep other users out and detach reused parents."""
    rng = random.Random(71101)
    owner = "fuzz-user"
    monkeypatch.setattr(psutil, "Process", lambda: SimpleNamespace(username=lambda: owner))

    for _ in range(200):
        pids = rng.sample(range(10000, 90000), rng.randint(2, 20))
        starts = {pid: float(rng.randint(100, 10000)) for pid in pids}
        rows = []
        for pid in pids:
            parent = rng.choice([1, *pids])
            user = rng.choice([owner, owner, "different-user", None])
            rows.append(_ProcessRow(pid, parent, starts[pid], user))
        monkeypatch.setattr(psutil, "process_iter", lambda *_args, rows=rows, **_kw: iter(rows))

        got = nh.win_processes()
        expected = {
            row.info["pid"]: (row.info["ppid"], row.info["create_time"], row.info["name"])
            for row in rows
            if row.info["username"] == owner and row.info["create_time"]
            and row.info["pid"] != __import__("os").getpid()
        }
        for pid, (ppid, started, name) in list(expected.items()):
            parent = expected.get(ppid)
            if parent is not None and parent[1] > started:
                expected[pid] = (0, started, name)
        assert got == expected


class _EnvironmentProcess:
    def __init__(self, pid: int, env: dict[str, str] | None, error: type[Exception] | None):
        self.pid = pid
        self._env = env
        self._error = error

    def oneshot(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def ppid(self):
        return 1

    def cmdline(self):
        return ["python", "untrusted-worker.py"]

    def environ(self):
        if self._error:
            raise self._error(pid=self.pid)
        return self._env

    def cwd(self):
        return "/tmp/untrusted-worker"

    def exe(self):
        return "/usr/bin/python"


def test_pid_and_environment_generated_rows_do_not_expand_sweep(monkeypatch) -> None:
    """PID-only scans and unreadable or forged env tags never authorize a halt target."""
    rng = random.Random(71102)
    errors = (None, psutil.AccessDenied, psutil.NoSuchProcess)
    install = nh.Install(prefix="/opt/kollab")

    for _ in range(200):
        pids = rng.sample(range(10000, 90000), rng.randint(1, 20))
        agent_pid = pids[0]
        envs = {}
        for index, pid in enumerate(pids):
            env = {}
            error = None
            if index:
                unrelated_pids = pids[1:]
                if rng.choice([True, False]):
                    env["KOLLAB_AGENT_PID"] = str(rng.choice(unrelated_pids))
                if rng.choice([True, False]):
                    env["KOLLAB_OWNER_PID"] = str(rng.choice(unrelated_pids))
                error = rng.choice(errors)
            envs[pid] = _EnvironmentProcess(pid, env, error)
        monkeypatch.setattr(psutil, "pids", lambda pids=pids: pids)
        monkeypatch.setattr(psutil, "Process", lambda pid: envs[pid])

        rows = [nh._win_read_process(pid) for pid in psutil.pids()]
        rows = [row for row in rows if row is not None]
        tree = {pid: (1, pid, pid) for pid in pids}
        info = {pid: (100.0 + index, "worker", "") for index, pid in enumerate(pids)}
        selected = nh.select_sweep(
            rows,
            install=install,
            scopes=["/tmp/untrusted-worker"],
            agent_pids=[agent_pid],
            tree=tree,
            info=info,
        )
        assert {process.pid for process in selected} == {agent_pid}


def test_recursive_children_generated_trees_survive_races_safely(monkeypatch) -> None:
    """Recursive child snapshots retain only returned, identity-readable handles."""
    rng = random.Random(71103)

    class Child:
        def __init__(self, pid: int, parent: int, live: bool, readable: bool):
            self.pid = pid
            self.parent_pid = parent
            self.live = live
            self.readable = readable

        def is_running(self):
            return self.live

        def ppid(self):
            return self.parent_pid

        def children(self, recursive=True):
            assert recursive is True
            return []

        def create_time(self):
            if not self.readable:
                raise psutil.NoSuchProcess(self.pid)
            return float(self.pid)

    for _ in range(200):
        root = Child(1000, 1, True, True)
        children = [
            Child(pid, rng.choice([root.pid, 1, 2]), rng.choice([True, True, False]), rng.choice([True, True, False]))
            for pid in rng.sample(range(1001, 5000), rng.randint(1, 20))
        ]
        children[:0] = [Child(5001, root.pid, True, False), Child(5002, root.pid, True, True)]
        # The children() result is the OS snapshot: reparented processes are
        # omitted, and a disappearing or unreadable child can race the read.
        returned = [child for child in children if child.parent_pid == root.pid]
        monkeypatch.setattr(root, "children", lambda recursive=True, returned=returned: returned)
        runner = object.__new__(ACPRunner)
        runner._owned_processes = {root.pid: root}

        runner._refresh_process_tree()

        recorded = runner._owned_processes
        assert recorded[root.pid] is root
        assert set(recorded) <= {root.pid, *(child.pid for child in returned)}
        for child in returned:
            if child.readable:
                assert recorded.get(child.pid) is child
