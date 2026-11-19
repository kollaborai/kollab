"""Unit tests for the fail-closed host-process scan policy."""

import ast
import base64
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.process_scan_guard import (
    HOST_PROCESS_SCAN_TESTS,
    guarded_process_enumeration,
    host_process_scan_environment_errors,
    host_process_scans_allowed,
    install_test_process_scan_guard,
    is_process_table_enumeration_command,
)

CI_ENVIRONMENT = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_WORKFLOW": "CI",
    "GITHUB_JOB": "tests",
    "GITHUB_REPOSITORY": "kollaborai/kollab",
    "GITHUB_EVENT_NAME": "pull_request",
    "GITHUB_WORKFLOW_REF": ("kollaborai/kollab/.github/workflows/ci.yml@refs/pull/570/merge"),
    "RUNNER_OS": "Linux",
    "RUNNER_ENVIRONMENT": "github-hosted",
}


def _fake_os(calls):
    return SimpleNamespace(
        system=lambda command, *args, **kwargs: calls.append(("system", command)) or "system result",
        popen=lambda command, *args, **kwargs: calls.append(("popen", command)) or "popen result",
    )


def test_scan_manifest_names_existing_live_process_tests():
    tests_root = Path(__file__).parents[1]
    for module_path, expected_names in HOST_PROCESS_SCAN_TESTS.items():
        module_path = tests_root / module_path
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        declared_names = {
            node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert expected_names <= declared_names


def test_host_process_scans_require_explicit_option_and_sanctioned_ci_metadata():
    assert host_process_scans_allowed(True, CI_ENVIRONMENT)
    assert not host_process_scans_allowed(False, CI_ENVIRONMENT)


@pytest.mark.parametrize(
    ("job", "event"),
    [
        ("tests", "merge_group"),
        ("supporting_tests", "pull_request"),
        ("supporting_tests", "merge_group"),
    ],
)
def test_host_process_scans_allow_guarded_tests_in_each_ci_job(job, event):
    environ = dict(CI_ENVIRONMENT, GITHUB_JOB=job, GITHUB_EVENT_NAME=event)
    assert host_process_scans_allowed(True, environ)


def test_runner_environment_alone_cannot_authorize_host_process_scans():
    assert not host_process_scans_allowed(True, {"RUNNER_ENVIRONMENT": "github-hosted"})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GITHUB_JOB", "another-job"),
        ("GITHUB_WORKFLOW", "Other workflow"),
        ("GITHUB_REPOSITORY", "someone/fork"),
        ("RUNNER_OS", "macOS"),
        ("RUNNER_ENVIRONMENT", "self-hosted"),
        ("GITHUB_EVENT_NAME", "schedule"),
        ("GITHUB_WORKFLOW_REF", "someone/fork/.github/workflows/ci.yml@refs/heads/main"),
    ],
)
def test_mismatched_runner_or_job_metadata_fails_closed(key, value):
    environ = dict(CI_ENVIRONMENT)
    environ[key] = value
    assert not host_process_scans_allowed(True, environ)
    assert key in host_process_scan_environment_errors(environ)


def test_process_table_detector_blocks_global_ps_but_allows_pid_targeting():
    assert is_process_table_enumeration_command(["ps", "-A", "-o", "pid,ppid"])
    assert is_process_table_enumeration_command(["/bin/ps", "-U", "501", "-o", "pid"])

    assert is_process_table_enumeration_command(["/bin/ps", "-axo", "pid,ppid"])
    assert is_process_table_enumeration_command(["/bin/ps", "-E", "-axww", "-o", "pid=,command="])
    for command in (
        ["ps", "-p", "42", "auxww"],
        ["ps", "-p", "42", "axww"],
        ["ps", "-p42", "auxww"],
        ["ps", "--pid=42", "axww"],
        ["ps", "-p", "42", "uaxww"],
        ["ps", "-p", "42", "-xww"],
        ["ps", "-p", "42", "-d"],
        ["ps", "-p", "42", "-N"],
        "ps -p 42 auxww",
    ):
        assert is_process_table_enumeration_command(command), command
    assert is_process_table_enumeration_command("/bin/sh -c 'ps -A -o pid'")
    assert is_process_table_enumeration_command(["/bin/bash", "-lc", "ps -A"])
    assert is_process_table_enumeration_command(["pgrep", "-af", "python"])
    assert is_process_table_enumeration_command(["top", "-l", "1"])
    assert is_process_table_enumeration_command(["pstree", "-p"])
    assert is_process_table_enumeration_command(["lsof", "-i"])
    assert is_process_table_enumeration_command(["lsof", "-p", "^1"])
    assert is_process_table_enumeration_command(["lsof", "-p", "0"])
    assert is_process_table_enumeration_command(["lsof", "-p", "42,^99"])
    assert is_process_table_enumeration_command(["lsof", "-p", "42", "-u", "root"])
    assert is_process_table_enumeration_command(["lsof", "-p", "42", "-i"])
    assert is_process_table_enumeration_command(["lsof", "-p4242", "-u", "root"])
    assert not is_process_table_enumeration_command(["lsof", "-p", "4242"])
    assert not is_process_table_enumeration_command(["lsof", "-p", "42,99"])
    assert not is_process_table_enumeration_command(["lsof", "-p4242"])
    assert is_process_table_enumeration_command(["ps"])
    assert is_process_table_enumeration_command(["ps", "-o", "pid"])
    assert not is_process_table_enumeration_command(["ps", "-p", "4242", "-o", "pid"])
    assert not is_process_table_enumeration_command(["ps", "-p4242", "-o", "pid"])
    assert not is_process_table_enumeration_command(["ps", "--pid=4242", "--format=pid"])
    assert not is_process_table_enumeration_command(["ps", "-p", "42,99", "-o", "pid"])
    assert not is_process_table_enumeration_command(["ps", "-p", "42", "-o", "auxww"])
    assert is_process_table_enumeration_command(["ps", "-p", "0", "-o", "pid"])
    assert is_process_table_enumeration_command(["ps", "-p", "42", "-o", "pid", "auxww"])
    assert is_process_table_enumeration_command("echo $(ps -A)")
    assert is_process_table_enumeration_command("echo `pgrep python`")
    assert is_process_table_enumeration_command(b"ps -A")
    assert is_process_table_enumeration_command([b"ps", b"-p", b"42", b"--ppid", b"1"])
    assert is_process_table_enumeration_command(Path("/bin/ps"))
    assert not is_process_table_enumeration_command(["/usr/bin/python3", "-c", "print('not a process scan')"])


@pytest.mark.parametrize("command", [["echo", "ps"], ["git", "log", "lsof"]])
def test_process_table_detector_ignores_process_tool_names_in_arguments(command):
    assert not is_process_table_enumeration_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "ps -A 'unterminated",
        r'cmd /c "C:\Windows\System32\TASKLIST.EXE',
        r'cmd /c C:\Windows\System32\TASKLIST.EXE"',
        'sh -c "ps -A',
        ["/bin/sh", "-c", "ps -A 'unterminated"],
        object(),
    ],
)
def test_process_table_detector_fails_closed_when_command_is_unparseable(command):
    assert is_process_table_enumeration_command(command)


def test_process_table_detector_keeps_empty_commands_non_enumerating():
    assert not is_process_table_enumeration_command("")
    assert not is_process_table_enumeration_command([])


@pytest.mark.parametrize(
    "command",
    [
        "tasklist.exe",
        r"cmd /c C:\Windows\System32\TASKLIST.EXE",
        r'cmd /k "TASKLIST.EXE /FO CSV"',
        r'cmd /c "TASKLIST.EXE /FO CSV"',
        ["TASKLIST.EXE"],
        [r"C:\Windows\System32\tasklist.exe"],
        [r"C:\Windows\System32\TASKLIST.EXE"],
        r"C:\Windows\System32\TASKLIST.EXE /FO CSV",
        r'"C:\Program Files\Windows NT\Accessories\TASKLIST.EXE" /FO CSV',
    ],
)
def test_process_table_detector_blocks_windows_tasklist_executable_spellings(command):
    assert is_process_table_enumeration_command(command)


def test_process_table_detector_recurses_into_powershell_command_payloads():
    script = r"& 'C:\Windows\System32\TASKLIST.EXE' /FO CSV"
    for executable in ("powershell.exe", "pwsh.exe"):
        command = f'{executable} -NoProfile -Command "{script}"'
        assert is_process_table_enumeration_command(command)
        assert is_process_table_enumeration_command([executable, "-NoProfile", "-Command", script])
        assert is_process_table_enumeration_command(
            [executable, "-NoProfile", "-Command", "Get-Process"]
        )
        assert not is_process_table_enumeration_command(
            [executable, "-NoProfile", "-Command", "Get-Date"]
        )
        for process_provider_command in (
            "Get-CimInstance Win32_Process",
            "Get-CimInstance -ClassName Win32_Process",
            "Get-WmiObject Win32_Process",
            "gcim Win32_Process",
            "gwmi Win32_Process",
            'Get-CimInstance -Query "SELECT * FROM Win32_Process"',
            'Get-WmiObject -Query "SELECT * FROM Win32_Process"',
        ):
            assert is_process_table_enumeration_command(
                [executable, "-NoProfile", "-Command", process_provider_command]
            )

    for process_provider_script in (
        "Get-CimInstance Win32_Process",
        "Get-WmiObject Win32_Process",
        "[System.Diagnostics.Process]::GetProcesses()",
        "Invoke-Expression 'Get-CimInstance Win32_Process'",
        r"CimCmdlets\Get-CimInstance Win32_Process",
        "$cmd='Get-CimInstance'; & $cmd -ClassName Win32_Process",
    ):
        encoded_cim = base64.b64encode(
            process_provider_script.encode("utf-16-le")
        ).decode("ascii")
        assert is_process_table_enumeration_command(["pwsh.exe", "-NoProfile", "-EncodedCommand", encoded_cim])

    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    assert is_process_table_enumeration_command(["pwsh.exe", "-NoProfile", "-EncodedCommand", encoded])
    assert is_process_table_enumeration_command(["pwsh.exe", "-NoProfile", "-EncodedCommand", "not-base64"])


@pytest.mark.parametrize(
    "script",
    [
        "[System.Diagnostics.Process]::GetProcesses()",
        "[System.Diagnostics.Process]::GetProcessesByName('python')",
        "Invoke-Expression 'Get-CimInstance Win32_Process'",
        "iex 'Get-CimInstance Win32_Process'",
        r"CimCmdlets\Get-CimInstance Win32_Process",
    ],
)
def test_powershell_alternate_process_scans_fail_closed(script):
    for executable in ("powershell.exe", "pwsh.exe"):
        command = [executable, "-NoProfile", "-Command", script]
        assert is_process_table_enumeration_command(command), command
        command_string = f'{executable} -NoProfile -Command "{script}"'
        assert is_process_table_enumeration_command(command_string), command_string
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        encoded_command = [executable, "-NoProfile", "-EncodedCommand", encoded]
        assert is_process_table_enumeration_command(encoded_command), encoded_command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["wmic.exe", "process", "list", "brief"], True),
        ("wmic.exe process list brief", True),
        (r"C:\Windows\System32\wbem\wmic.exe process list brief", True),
        (["wmic.exe", "path", "Win32_Process", "get", "ProcessId"], True),
        (["wmic.exe", "computersystem", "get", "name"], False),
        (
            ["wmic.exe", "/query", "SELECT * FROM Win32_Process WHERE Name='python.exe'"],
            True,
        ),
        (["wmic.exe", "/query", "SELECT * FROM %WMI_CLASS%"], True),
        (["wmic.exe", "/query:SELECT * FROM %WMI_CLASS%"], True),
        (["wmic.exe", "unknownclass", "get", "Name"], True),
        (["wmic.exe", "/query", "SELECT * FROM Win32_ComputerSystem"], False),
        (
            ["wmic.exe", "process", "where", "Name='python.exe'", "get", "Name,ProcessId"],
            True,
        ),
    ],
)
def test_wmic_fails_closed_except_allowlisted_computer_system(command, expected):
    assert is_process_table_enumeration_command(command) is expected


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_powershell_get_date_requires_no_profile(executable):
    encoded = base64.b64encode("Get-Date".encode("utf-16-le")).decode("ascii")

    assert is_process_table_enumeration_command([executable, "-Command", "Get-Date"])
    assert is_process_table_enumeration_command([executable, "-EncodedCommand", encoded])
    assert not is_process_table_enumeration_command(
        [executable, "-NoProfile", "-Command", "Get-Date"]
    )
    assert not is_process_table_enumeration_command(
        [executable, "-NoProfile", "-EncodedCommand", encoded]
    )



@pytest.mark.parametrize(
    "script",
    [
        "Get-Command Get-Process",
        "Get-Process -PassThru",
        "Start-Process -FilePath notepad.exe -PassThru",
        "[System.Diagnostics.Process]::GetProcessesByName('python')",
        "$command = 'Get-Process'; & $command",
        "Write-Output harmless",
        "$cmd='Get-CimInstance'; & $cmd -ClassName Win32_Process",
    ],
)
def test_unallowlisted_powershell_payloads_fail_closed(script):
    for executable in ("powershell.exe", "pwsh.exe"):
        command = [executable, "-NoProfile", "-Command", script]
        assert is_process_table_enumeration_command(command), command
        command_string = f'{executable} -NoProfile -Command "{script}"'
        assert is_process_table_enumeration_command(command_string), command_string
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        encoded_command = [executable, "-NoProfile", "-EncodedCommand", encoded]
        assert is_process_table_enumeration_command(encoded_command), encoded_command


@pytest.mark.parametrize(
    "command",
    [
        ["pwsh.exe", "-File", "process_scan.ps1"],
        ["pwsh.exe", "-File", "process_scan.ps1", "-Command", "Get-Date"],
        ["pwsh.exe", "-NoProfile"],
        ["pwsh.exe", "-Com", "Get-Date"],
    ],
)
def test_uninspected_powershell_invocation_modes_fail_closed(command):
    assert is_process_table_enumeration_command(command), command


def test_unrecognized_powershell_host_family_fails_closed():
    script = "Get-CimInstance -ClassName Win32_Process"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    for executable in (
        "pwsh-preview",
        "pwsh-preview.exe",
        "powershell-preview.exe",
        "powershell_ise.exe",
        "powershell7.exe",
        r"C:\Program Files\PowerShell\7\pwsh-preview.exe",
    ):
        assert is_process_table_enumeration_command(
            [executable, "-NoProfile", "-Command", script]
        )
        assert is_process_table_enumeration_command(
            [executable, "-NoProfile", "-EncodedCommand", encoded]
        )


def test_installed_guard_blocks_windows_tasklist_paths_before_launch(monkeypatch):
    calls = []

    class FakeProcess:
        pid = 1234

        def children(self, *args, **kwargs):
            return []

        def parents(self):
            return []

        def environ(self):
            return {}

    fake_psutil = SimpleNamespace(Process=FakeProcess, process_iter=lambda *args, **kwargs: iter(()), pids=lambda: [])
    fake_subprocess = SimpleNamespace(Popen=lambda *args, **kwargs: calls.append((args, kwargs)) or "launched")
    fake_os = _fake_os(calls)
    install_test_process_scan_guard(fake_psutil, fake_subprocess, monkeypatch, os_module=fake_os)

    tasklist_strings = (
        r"C:\Windows\System32\TASKLIST.EXE /FO CSV",
        r'"C:\Program Files\Windows NT\Accessories\TASKLIST.EXE" /FO CSV',
        r"cmd /c C:\Windows\System32\TASKLIST.EXE",
        r'cmd /c "C:\Program Files\Windows NT\Accessories\TASKLIST.EXE" /FO CSV',
        r'cmd /c "C:\Windows\System32\TASKLIST.EXE /FO CSV"',
        r'cmd /c C:\Windows\System32\TASKLIST.EXE"',
        r'''powershell.exe -NoProfile -Command "& 'C:\Windows\System32\TASKLIST.EXE' /FO CSV"''',
        r'''pwsh.exe -NoProfile -Command "& 'C:\Windows\System32\TASKLIST.EXE' /FO CSV"''',
    )
    for command in (
        ["tasklist.exe"],
        [r"C:\Windows\System32\TASKLIST.EXE"],
        *tasklist_strings,
    ):
        with pytest.raises(RuntimeError, match="blocked for unmarked test"):
            fake_subprocess.Popen(command)

    for command in tasklist_strings:
        for operation in (fake_os.system, fake_os.popen):
            with pytest.raises(RuntimeError, match="blocked for unmarked test"):
                operation(command)

    encoded = base64.b64encode(
        r"& 'C:\Windows\System32\TASKLIST.EXE' /FO CSV".encode("utf-16-le")
    ).decode("ascii")
    with pytest.raises(RuntimeError, match="blocked for unmarked test"):
        fake_subprocess.Popen(["pwsh.exe", "-NoProfile", "-EncodedCommand", encoded])

    encoded_get_date = base64.b64encode("Get-Date".encode("utf-16-le")).decode("ascii")
    preview_scan = "Get-CimInstance -ClassName Win32_Process"
    encoded_preview_scan = base64.b64encode(preview_scan.encode("utf-16-le")).decode("ascii")
    for command in (
        ["powershell.exe", "-NoProfile", "-Command", "Get-CimInstance Win32_Process"],
        ["powershell.exe", "-NoProfile", "-Command", "Get-CimInstance -ClassName Win32_Process"],
        ["powershell.exe", "-NoProfile", "-Command", "Get-WmiObject Win32_Process"],
        ["powershell.exe", "-NoProfile", "-Command", "[System.Diagnostics.Process]::GetProcesses()"],
        ["wmic.exe", "process", "list", "brief"],
        ["pwsh.exe", "-NoProfile", "-Command", "Get-Command Get-Process"],
        ["pwsh.exe", "-NoProfile", "-Command", "Write-Output harmless"],
        ["pwsh.exe", "-NoProfile", "-Command", "$command = 'Get-Process'; & $command"],
        ["pwsh.exe", "-File", "process_scan.ps1"],
        ["wmic.exe", "/query", "SELECT * FROM %WMI_CLASS%"],
        ["wmic.exe", "unknownclass", "get", "Name"],
        ["powershell.exe", "-NoProfile", "-Command", "Invoke-Expression 'Get-CimInstance Win32_Process'"],
        ["powershell.exe", "-NoProfile", "-Command", r"CimCmdlets\Get-CimInstance Win32_Process"],
        ["pwsh.exe", "-Command", "Get-Date"],
        ["pwsh.exe", "-EncodedCommand", encoded_get_date],
        ["pwsh-preview.exe", "-NoProfile", "-Command", preview_scan],
        ["pwsh-preview.exe", "-NoProfile", "-EncodedCommand", encoded_preview_scan],
        ["pwsh.exe", "-NoProfile", "-Command", "$cmd='Get-CimInstance'; & $cmd -ClassName Win32_Process"],
    ):
        with pytest.raises(RuntimeError, match="blocked for unmarked test"):
            fake_subprocess.Popen(command)

    for process_provider_script in (
        "Get-CimInstance Win32_Process",
        "Get-WmiObject Win32_Process",
        "[System.Diagnostics.Process]::GetProcesses()",
        "Invoke-Expression 'Get-CimInstance Win32_Process'",
        r"CimCmdlets\Get-CimInstance Win32_Process",
        "$cmd='Get-CimInstance'; & $cmd -ClassName Win32_Process",
    ):
        encoded_cim = base64.b64encode(
            process_provider_script.encode("utf-16-le")
        ).decode("ascii")
        with pytest.raises(RuntimeError, match="blocked for unmarked test"):
            fake_subprocess.Popen(["pwsh.exe", "-NoProfile", "-EncodedCommand", encoded_cim])

    assert calls == []
    safe_wmic_commands = [
        ["wmic.exe", "computersystem", "get", "name"],
        ["wmic.exe", "/query", "SELECT * FROM Win32_ComputerSystem"],
    ]
    for command in safe_wmic_commands:
        assert fake_subprocess.Popen(command) == "launched"

    safe_powershell_commands = [
        ["pwsh.exe", "-NoProfile", "-Command", "Get-Date"],
        ["pwsh.exe", "-NoProfile", "-EncodedCommand", encoded_get_date],
    ]
    for command in safe_powershell_commands:
        assert fake_subprocess.Popen(command) == "launched"
    expected_calls = [
        ((command,), {}) for command in safe_wmic_commands + safe_powershell_commands
    ]
    assert calls == expected_calls


@pytest.mark.parametrize(
    "selector",
    [
        ["--ppid", "1"],
        ["--sid", "1"],
        ["-g", "1"],
        ["-G", "1"],
        ["-t", "pts/1"],
        ["-C", "python"],
        ["--user", "root"],
    ],
)
def test_pid_targeted_ps_rejects_other_selectors(selector):
    assert is_process_table_enumeration_command(["ps", "-p", "42", *selector])


def test_docker_container_listing_is_not_host_process_enumeration():
    assert not is_process_table_enumeration_command(["docker", "ps", "-q"])
    assert not is_process_table_enumeration_command(["docker", "container", "ps", "-q"])
    assert not is_process_table_enumeration_command(["docker", "compose", "ps", "-q"])
    assert not is_process_table_enumeration_command(["docker-compose", "ps", "-q"])
    assert not is_process_table_enumeration_command(["/bin/sh", "-c", 'echo "$(docker ps -q)"'])
    assert is_process_table_enumeration_command(["/bin/sh", "-c", "docker exec service ps -A"])


def test_unmarked_process_enumeration_fails_before_original_call():
    calls = []
    guarded = guarded_process_enumeration("psutil.process_iter", lambda: calls.append("enumerated"), allowed=False)

    with pytest.raises(RuntimeError, match="blocked for unmarked test"):
        guarded()

    assert calls == []


def test_authorized_process_enumeration_wrapper_preserves_original_call():
    def original(value):
        return value + 1

    guarded = guarded_process_enumeration("psutil.process_iter", original, allowed=True)
    assert guarded is original
    assert guarded(4) == 5


def test_installed_guard_blocks_enumeration_apis_before_the_original_call(monkeypatch):
    calls = []

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def children(self, *args, **kwargs):
            calls.append(("children", self.pid, args, kwargs))
            return []

        def parents(self):
            calls.append(("parents", self.pid))
            return []

        def parent(self):
            calls.append(("parent", self.pid))
            return None

        def environ(self):
            calls.append(("environ", self.pid))
            return {"FAKE": "value"}

    fake_psutil = SimpleNamespace(
        Process=FakeProcess,
        process_iter=lambda *args, **kwargs: calls.append(("process_iter", args, kwargs)) or iter(()),
        pids=lambda: calls.append(("pids",)) or [1234],
    )
    fake_subprocess = SimpleNamespace(Popen=lambda *args, **kwargs: calls.append(("Popen", args, kwargs)) or "launched")
    fake_os = _fake_os(calls)

    install_test_process_scan_guard(fake_psutil, fake_subprocess, monkeypatch, os_module=fake_os)

    for operation in (
        fake_psutil.process_iter,
        fake_psutil.pids,
        lambda: FakeProcess(1234).children(recursive=True),
        lambda: FakeProcess(1234).parent(),
        lambda: FakeProcess(1234).parents(),
        lambda: FakeProcess(1234).environ(),
        lambda: fake_subprocess.Popen(["/bin/ps", "-A", "-o", "pid="]),
        lambda: fake_subprocess.Popen(["ps", "-p", "42", "auxww"]),
        lambda: fake_os.system("ps -A"),
        lambda: fake_os.system("ps -p 42 axww"),
        lambda: fake_os.popen('echo "$(ps -p 42 auxww)"'),
        lambda: fake_os.popen("pgrep -af python"),
        lambda: fake_os.system("echo $(ps -A)"),
        lambda: fake_subprocess.Popen("ps -A 'unterminated", shell=True),
        lambda: fake_subprocess.Popen(
            "display-name 'unterminated", executable="/bin/echo"
        ),
        lambda: fake_os.system("ps -A 'unterminated"),
        lambda: fake_os.popen('echo "$(ps -A)'),
        lambda: fake_subprocess.Popen(object()),
    ):
        with pytest.raises(RuntimeError):
            operation()

    assert calls == []


def test_installed_guard_allows_environment_reads_for_the_test_process(monkeypatch):
    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def children(self, *args, **kwargs):
            return []

        def parents(self):
            return []

        def environ(self):
            return {"FAKE": "value"}

    fake_psutil = SimpleNamespace(Process=FakeProcess, process_iter=lambda *args, **kwargs: iter(()), pids=lambda: [])
    fake_subprocess = SimpleNamespace(Popen=lambda *args, **kwargs: "launched")
    fake_os = _fake_os([])
    install_test_process_scan_guard(fake_psutil, fake_subprocess, monkeypatch, os_module=fake_os)
    assert FakeProcess(os.getpid()).environ() == {"FAKE": "value"}


def test_installed_guard_blocks_broad_ps_before_launch(monkeypatch):
    calls = []

    class FakeProcess:
        pid = 1234

        def children(self, *args, **kwargs):
            return []

        def parents(self):
            return []

        def environ(self):
            return {}

    fake_psutil = SimpleNamespace(Process=FakeProcess, process_iter=lambda *args, **kwargs: iter(()), pids=lambda: [])
    fake_subprocess = SimpleNamespace(Popen=lambda *args, **kwargs: calls.append((args, kwargs)) or "launched")
    fake_os = _fake_os(calls)
    install_test_process_scan_guard(fake_psutil, fake_subprocess, monkeypatch, os_module=fake_os)
    with pytest.raises(RuntimeError, match="blocked for unmarked test"):
        fake_subprocess.Popen(["/bin/ps", "-A", "-o", "pid="])
    assert calls == []


def test_installed_guard_blocks_process_scans_through_spawn_and_exec_apis(monkeypatch):
    calls = []

    def record(name):
        def operation(*args, **kwargs):
            calls.append((name, args, kwargs))
            return 0

        return operation

    class FakeProcess:
        pid = 1234

        def children(self, *args, **kwargs):
            return []

        def parents(self):
            return []

        def environ(self):
            return {}

    fake_psutil = SimpleNamespace(
        Process=FakeProcess,
        process_iter=lambda *args, **kwargs: iter(()),
        pids=lambda: [],
    )
    fake_subprocess = SimpleNamespace(Popen=record("Popen"))
    fake_os = _fake_os(calls)
    fake_os.spawnv = record("spawnv")
    fake_os.spawnve = record("spawnve")
    fake_os.spawnvp = record("spawnvp")
    fake_os.spawnvpe = record("spawnvpe")
    fake_os.spawnl = record("spawnl")
    fake_os.spawnle = record("spawnle")
    fake_os.spawnlp = record("spawnlp")
    fake_os.spawnlpe = record("spawnlpe")
    fake_os.posix_spawn = record("posix_spawn")
    fake_os.posix_spawnp = record("posix_spawnp")
    fake_os.execv = record("execv")
    fake_os.execve = record("execve")
    fake_os.execvp = record("execvp")
    fake_os.execvpe = record("execvpe")
    fake_os.execl = record("execl")
    fake_os.execle = record("execle")
    fake_os.execlp = record("execlp")
    fake_os.execlpe = record("execlpe")
    install_test_process_scan_guard(fake_psutil, fake_subprocess, monkeypatch, os_module=fake_os)

    operations = (
        lambda: fake_os.spawnv(0, "/bin/ps", ["ps", "-p", "42", "--ppid", "1"]),
        lambda: fake_os.spawnve(0, "ps", ["ps", "-p", "42", "--ppid", "1"], {}),
        lambda: fake_os.spawnvp(0, "ps", ["ps", "-p", "42", "--ppid", "1"]),
        lambda: fake_os.spawnvpe(0, "ps", ["ps", "-p", "42", "--ppid", "1"], {}),
        lambda: fake_os.spawnl(0, "ps", "ps", "-p", "42", "-g", "1"),
        lambda: fake_os.spawnle(0, "ps", "ps", "-p", "42", "-G", "1", {}),
        lambda: fake_os.spawnlp(0, "ps", "ps", "-p", "42", "-t", "pts/1"),
        lambda: fake_os.spawnlpe(0, "ps", "ps", "-p", "42", "-g", "1", {}),
        lambda: fake_os.posix_spawn("/bin/ps", ["ps", "-p", "42", "-C", "python"], {}),
        lambda: fake_os.posix_spawnp("ps", ["ps", "-p", "42", "--sid", "1"], {}),
        lambda: fake_os.execv("/bin/ps", ["ps", "-p", "42", "--sid", "1"]),
        lambda: fake_os.execve("/bin/ps", ["ps", "-p", "42", "--sid", "1"], {}),
        lambda: fake_os.execvp("ps", ["ps", "-p", "42", "-t", "pts/1"]),
        lambda: fake_os.execvpe("ps", ["ps", "-p", "42", "-t", "pts/1"], {}),
        lambda: fake_os.execl("ps", "ps", "-p", "42", "-C", "python"),
        lambda: fake_os.execle("ps", "ps", "-p", "42", "-C", "python", {}),
        lambda: fake_os.execlp("ps", "ps", "-p", "42", "-C", "python"),
        lambda: fake_os.execlpe("ps", "ps", "-p", "42", "--user", "root", {}),
        # Unknown spawn/exec argument shapes must fail closed before launch.
        lambda: fake_os.spawnv(0, "/bin/ps", "ps -A"),
        lambda: fake_os.posix_spawn("/bin/ps", object(), {}),
        lambda: fake_os.execv("/bin/ps", None),
    )
    for operation in operations:
        with pytest.raises(RuntimeError, match="blocked for unmarked test"):
            operation()

    assert calls == []


def test_installed_guard_preserves_non_scan_spawn_and_exec_calls(monkeypatch):
    calls = []

    def record(name):
        def operation(*args, **kwargs):
            calls.append((name, args, kwargs))
            return 17

        return operation

    class FakeProcess:
        pid = 1234

        def children(self, *args, **kwargs):
            return []

        def parents(self):
            return []

        def environ(self):
            return {}

    fake_psutil = SimpleNamespace(Process=FakeProcess, process_iter=lambda *args, **kwargs: iter(()), pids=lambda: [])
    fake_subprocess = SimpleNamespace(Popen=record("Popen"))
    fake_os = _fake_os(calls)
    fake_os.spawnlp = record("spawnlp")
    fake_os.posix_spawn = record("posix_spawn")
    fake_os.execv = record("execv")
    install_test_process_scan_guard(fake_psutil, fake_subprocess, monkeypatch, os_module=fake_os)

    assert fake_os.spawnlp(0, "sleep", "sleep", "30") == 17
    assert fake_os.posix_spawn("/usr/bin/python3", ["python3", "-c", "pass"], {}) == 17
    assert fake_os.execv("/usr/bin/python3", ["python3", "-c", "pass"]) == 17
    assert [name for name, _, _ in calls] == ["spawnlp", "posix_spawn", "execv"]


@pytest.mark.parametrize("positional", [False, True], ids=["keyword", "positional"])
def test_popen_executable_override_cannot_hide_a_process_scan(monkeypatch, positional):
    calls = []

    class FakeProcess:
        pid = 1234

        def children(self, *args, **kwargs):
            return []

        def parents(self):
            return []

        def environ(self):
            return {}

    fake_psutil = SimpleNamespace(Process=FakeProcess, process_iter=lambda *args, **kwargs: iter(()), pids=lambda: [])
    fake_subprocess = SimpleNamespace(Popen=lambda *args, **kwargs: calls.append((args, kwargs)))
    fake_os = _fake_os(calls)
    install_test_process_scan_guard(fake_psutil, fake_subprocess, monkeypatch, os_module=fake_os)

    def popen(command):
        if positional:
            return fake_subprocess.Popen(command, -1, "/bin/ps")
        return fake_subprocess.Popen(command, executable="/bin/ps")

    with pytest.raises(RuntimeError, match="blocked for unmarked test"):
        popen(["display-name", "-A"])

    assert calls == []
    assert popen(["display-name", "-p", "42", "-o", "pid"]) is None
    assert len(calls) == 1


def test_installed_guard_allows_non_scan_os_commands_without_running_them(monkeypatch):
    calls = []

    class FakeProcess:
        pid = 1234

        def children(self, *args, **kwargs):
            return []

        def parents(self):
            return []

        def environ(self):
            return {}

    fake_psutil = SimpleNamespace(Process=FakeProcess, process_iter=lambda *args, **kwargs: iter(()), pids=lambda: [])
    fake_subprocess = SimpleNamespace(Popen=lambda *args, **kwargs: "launched")
    fake_os = _fake_os(calls)
    install_test_process_scan_guard(fake_psutil, fake_subprocess, monkeypatch, os_module=fake_os)

    assert fake_os.system("clear") == "system result"
    assert fake_os.popen("stty size") == "popen result"
    assert calls == [("system", "clear"), ("popen", "stty size")]
