"""Installer verification must inspect the executable selected for installation."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = Path(
    os.environ.get("KOLLAB_INSTALL_SCRIPT", str(REPO_ROOT / "install.sh"))
)


def _run_verification(
    tmp_path: Path,
    *,
    installed_dir: Path,
    path_dirs: list[Path],
    version: str = "",
    installed_version: str | None = None,
) -> subprocess.CompletedProcess[str]:
    functions = tmp_path / "installer-functions.sh"
    functions.write_text(
        INSTALL_SCRIPT.read_text().split('\nmain "$@"')[0] + "\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    if installed_version is not None:
        installed_dir.mkdir(parents=True, exist_ok=True)
        executable = installed_dir / "kollab"
        executable.write_text(
            f"#!/bin/sh\necho 'kollab {installed_version}'\n", encoding="utf-8"
        )
        executable.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "KOLLAB_VERSION": version,
            "PATH": os.pathsep.join(str(path) for path in path_dirs),
        }
    )
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; INSTALLED_DIR="$2"; verify_install',
            "installer-test",
            str(functions),
            str(installed_dir),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _fake_executable(directory: Path, version: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "kollab"
    executable.write_text(f"#!/bin/sh\necho 'kollab {version}'\n", encoding="utf-8")
    executable.chmod(0o755)
    return executable


def _write_executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _run_installer_method(
    tmp_path: Path,
    *,
    method: str,
    path_dirs: list[Path],
    extra_env: dict[str, str] | None = None,
    entrypoint: str = "method",
) -> subprocess.CompletedProcess[str]:
    functions = tmp_path / "installer-functions.sh"
    functions.write_text(
        INSTALL_SCRIPT.read_text().split('\nmain "$@"')[0] + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "KOLLAB_VERSION": "4.5.6",
            "KOLLAB_INSTALL_METHOD": method,
            "PATH": os.pathsep.join(str(path) for path in path_dirs),
        }
    )
    env.update(extra_env or {})
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; if [ "$3" = main ]; then main; else "try_$2" && verify_install; fi',
            "installer-test",
            str(functions),
            method,
            entrypoint,
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_pip_scripts_dir(
    tmp_path: Path,
    *,
    pip_executable: Path,
    path_dirs: list[Path],
    user_scripts: Path,
) -> subprocess.CompletedProcess[str]:
    functions = tmp_path / "installer-functions.sh"
    functions.write_text(
        INSTALL_SCRIPT.read_text().split('\nmain "$@"')[0] + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": os.pathsep.join(str(path) for path in path_dirs),
            "USER_SCRIPTS": str(user_scripts),
            "WRONG_USER_SCRIPTS": str(tmp_path / "Wrong Python User Scripts"),
        }
    )
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; pip_scripts_dir "$2" posix_user',
            "installer-test",
            str(functions),
            str(pip_executable),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_stale_path_command_does_not_verify_missing_install(tmp_path: Path) -> None:
    stale_dir = tmp_path / "stale-bin"
    _fake_executable(stale_dir, "0.1.0")

    result = _run_verification(
        tmp_path,
        installed_dir=tmp_path / "install-bin",
        path_dirs=[stale_dir, Path("/usr/bin"), Path("/bin")],
    )

    assert result.returncode != 0
    assert "Installation verified" not in result.stdout
    assert str(stale_dir / "kollab") in result.stdout


def test_missing_installed_executable_fails_even_when_path_has_kollab(
    tmp_path: Path,
) -> None:
    unrelated_dir = tmp_path / "unrelated-bin"
    _fake_executable(unrelated_dir, "1.0.0")

    result = _run_verification(
        tmp_path,
        installed_dir=tmp_path / "install-bin",
        path_dirs=[unrelated_dir, Path("/usr/bin"), Path("/bin")],
    )

    assert result.returncode != 0
    assert "Installation verified" not in result.stdout


def test_stale_path_entry_cannot_hide_a_different_installed_executable(
    tmp_path: Path,
) -> None:
    stale_dir = tmp_path / "stale-bin"
    installed_dir = tmp_path / "install-bin"
    _fake_executable(stale_dir, "0.1.0")
    _fake_executable(installed_dir, "2.0.0")

    result = _run_verification(
        tmp_path,
        installed_dir=installed_dir,
        path_dirs=[stale_dir, installed_dir, Path("/usr/bin"), Path("/bin")],
        version="2.0.0",
        installed_version=None,
    )

    assert result.returncode != 0
    assert "Another kollab comes first on PATH" in result.stdout
    assert str(installed_dir / "kollab") in result.stdout
    assert "Installation verified" not in result.stdout


def test_requested_version_must_match_installed_executable_exactly(
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "install-bin"
    result = _run_verification(
        tmp_path,
        installed_dir=installed_dir,
        path_dirs=[installed_dir, Path("/usr/bin"), Path("/bin")],
        version="1.2.3",
        installed_version="1.2.30",
    )

    assert result.returncode != 0
    assert "Installation verified" not in result.stdout
    assert "1.2.3" in result.stdout
    assert "1.2.30" in result.stdout


def test_custom_install_directory_reports_actual_executable(tmp_path: Path) -> None:
    custom_dir = tmp_path / "custom tools" / "bin"
    result = _run_verification(
        tmp_path,
        installed_dir=custom_dir,
        path_dirs=[custom_dir, Path("/usr/bin"), Path("/bin")],
        version="2.0.0",
        installed_version="2.0.0",
    )

    assert result.returncode == 0
    assert str(custom_dir / "kollab") in result.stdout
    assert "2.0.0" in result.stdout


def test_path_symlink_to_installed_executable_is_not_a_conflict(tmp_path: Path) -> None:
    installed_dir = tmp_path / "real-bin"
    path_alias = tmp_path / "path-alias"
    path_alias.symlink_to(installed_dir, target_is_directory=True)

    result = _run_verification(
        tmp_path,
        installed_dir=installed_dir,
        path_dirs=[path_alias, Path("/usr/bin"), Path("/bin")],
        installed_version="2.0.0",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Installation verified" in result.stdout


def test_normal_install_reports_matching_executable_and_version(tmp_path: Path) -> None:
    installed_dir = tmp_path / "install-bin"
    result = _run_verification(
        tmp_path,
        installed_dir=installed_dir,
        path_dirs=[installed_dir, Path("/usr/bin"), Path("/bin")],
        version="3.4.5",
        installed_version="3.4.5",
    )

    assert result.returncode == 0
    assert str(installed_dir / "kollab") in result.stdout
    assert "3.4.5" in result.stdout


def test_version_output_must_identify_kollab(tmp_path: Path) -> None:
    installed_dir = tmp_path / "install-bin"
    result = _run_verification(
        tmp_path,
        installed_dir=installed_dir,
        path_dirs=[installed_dir, Path("/usr/bin"), Path("/bin")],
        installed_version="unrelated-tool 3.4.5",
    )

    assert result.returncode != 0
    assert "Installation verified" not in result.stdout
    assert "Could not parse the version" in result.stdout


@pytest.mark.parametrize("manager", ["uv", "pipx"])
def test_managed_installs_verify_the_manager_configured_bin_directory(
    tmp_path: Path, manager: str
) -> None:
    tools_dir = tmp_path / "tools"
    bin_dir = tmp_path / f"{manager} custom bin"
    _fake_executable(bin_dir, "4.5.6")
    if manager == "uv":
        manager_body = f"""#!/bin/sh
if [ "$1" = tool ] && [ "$2" = dir ]; then
    echo '{bin_dir}'
    exit 0
fi
exit 0
"""
    else:
        manager_body = f"""#!/bin/sh
if [ "$1" = environment ]; then
    echo '{bin_dir}'
fi
exit 0
"""
    _write_executable(tools_dir / manager, manager_body)

    result = _run_installer_method(
        tmp_path,
        method=manager,
        path_dirs=[tools_dir, bin_dir, Path("/usr/bin"), Path("/bin")],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(bin_dir / "kollab") in result.stdout
    assert "4.5.6" in result.stdout


def test_main_fails_when_install_is_missing_from_a_fresh_shell_path(
    tmp_path: Path,
) -> None:
    tools_dir = tmp_path / "tools"
    bin_dir = tmp_path / "managed bin"
    _fake_executable(bin_dir, "4.5.6")
    _write_executable(
        tools_dir / "uv",
        f"#!/bin/sh\nif [ \"$1\" = tool ] && [ \"$2\" = dir ]; then echo '{bin_dir}'; fi\n",
    )

    result = _run_installer_method(
        tmp_path,
        method="uv",
        path_dirs=[tools_dir, Path("/usr/bin"), Path("/bin")],
        entrypoint="main",
    )

    assert result.returncode != 0
    assert "not discoverable in a new shell" in result.stdout
    assert "Add " + str(bin_dir) + " to PATH" in result.stdout


@pytest.mark.parametrize("user_install", [False, True])
def test_pip_installs_verify_the_interpreters_system_or_user_scripts_dir(
    tmp_path: Path, user_install: bool
) -> None:
    tools_dir = tmp_path / "tools"
    scripts_dir = tmp_path / ("user scripts" if user_install else "system scripts")
    _fake_executable(scripts_dir, "4.5.6")
    python = _write_executable(
        tools_dir / "python3",
        f"#!/bin/sh\nprintf '%s\\n' '{scripts_dir}'\n",
    )
    pip_script = f"""#!{python}
if [ "$1" = install ] && [ "$2" = --help ]; then
    echo '--user'
    exit 0
fi
if [ "$1" = install ] && [ "$2" = --user ]; then
    exit 0
fi
"""
    if user_install:
        pip_script += "exit 1\n"
    else:
        pip_script += "exit 0\n"
    _write_executable(tools_dir / "pip3", pip_script)

    result = _run_installer_method(
        tmp_path,
        method="pip",
        path_dirs=[tools_dir, scripts_dir, Path("/usr/bin"), Path("/bin")],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(scripts_dir / "kollab") in result.stdout
    assert "4.5.6" in result.stdout


@pytest.mark.parametrize("user_setting", ["environment", "config"])
def test_pip_user_configuration_verifies_the_user_scripts_dir(
    tmp_path: Path, user_setting: str
) -> None:
    tools_dir = tmp_path / "tools"
    system_scripts = tmp_path / "system scripts"
    user_scripts = tmp_path / "user scripts"
    python = _write_executable(
        tools_dir / "python3",
        f'''#!/bin/sh
if [ "$1" = "-c" ]; then
    if [ "$3" = "posix_user" ]; then echo '{user_scripts}'; else echo '{system_scripts}'; fi
    exit 0
fi
script="$1"
shift
exec /bin/sh "$script" "$@"
''',
    )
    _write_executable(
        tools_dir / "pip3",
        f'''#!{python}
if [ "$1" = "config" ] && [ "$2" = "get" ] && [ "$3" = "install.user" ]; then echo true; fi
if [ "$1" = "install" ] && [ "$2" = "--help" ]; then echo --user; fi
exit 0
''',
    )
    _fake_executable(user_scripts, "4.5.6")
    extra_env = {"PIP_USER": "1"} if user_setting == "environment" else {}

    result = _run_installer_method(
        tmp_path,
        method="pip",
        path_dirs=[tools_dir, user_scripts, Path("/usr/bin"), Path("/bin")],
        extra_env=extra_env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(user_scripts / "kollab") in result.stdout


def test_pip_env_split_shebang_resolves_the_interpreter(
    tmp_path: Path,
) -> None:
    tools_dir = tmp_path / "tools"
    scripts_dir = tmp_path / "custom scripts"
    _fake_executable(scripts_dir, "4.5.6")
    python = _write_executable(
        tools_dir / "python3",
        f"#!/bin/sh\nprintf '%s\\n' '{scripts_dir}'\n",
    )
    pip_script = _write_executable(
        tools_dir / "pip3",
        "#!/usr/bin/env -S python3 -I\n",
    )

    result = _run_installer_method(
        tmp_path,
        method="pip",
        path_dirs=[tools_dir, scripts_dir, Path("/usr/bin"), Path("/bin")],
    )

    assert python.exists()
    assert pip_script.exists()
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(scripts_dir / "kollab") in result.stdout
    assert "4.5.6" in result.stdout


def test_stalled_version_command_times_out(tmp_path: Path) -> None:
    functions = tmp_path / "installer-functions.sh"
    functions.write_text(
        INSTALL_SCRIPT.read_text().split('\nmain "$@"')[0] + "\n",
        encoding="utf-8",
    )
    installed_dir = tmp_path / "install-bin"
    installed_dir.mkdir()
    executable = installed_dir / "kollab"
    executable.write_text("#!/bin/sh\nexec sleep 20\n", encoding="utf-8")
    executable.chmod(0o755)
    env = os.environ.copy()
    env.update({"HOME": str(tmp_path), "PATH": f"{installed_dir}:/usr/bin:/bin"})

    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; INSTALLED_DIR="$2"; VERSION_CHECK_TIMEOUT_SECONDS=1; verify_install',
            "installer-test",
            str(functions),
            str(installed_dir),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=3,
    )

    assert result.returncode != 0
    assert "timed out" in result.stdout


def test_pip_shell_trampoline_resolves_interpreter_with_spaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools_dir = tmp_path / "tools"
    scripts_dir = tmp_path / "installed scripts"
    _fake_executable(scripts_dir, "4.5.6")
    python = _write_executable(
        tools_dir / "python with space",
        '#!/bin/sh\nif [ "$1" = "-c" ]; then echo "$SCRIPTS"; '
        'elif [ "$2" = install ] && [ "$3" = --help ]; then echo --user; fi\n',
    )
    _write_executable(
        tools_dir / "pip3",
        f"#!/bin/sh\n'''exec' \"{python}\" \"$0\" \"$@\"\n' '''\n",
    )

    result = _run_installer_method(
        tmp_path,
        method="pip",
        path_dirs=[tools_dir, scripts_dir, Path("/usr/bin"), Path("/bin")],
        extra_env={"SCRIPTS": str(scripts_dir)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(scripts_dir / "kollab") in result.stdout
    assert "4.5.6" in result.stdout


def test_pip_prefix_install_verifies_its_custom_scripts_directory(
    tmp_path: Path,
) -> None:
    tools_dir = tmp_path / "tools"
    prefix = tmp_path / "custom prefix"
    prefix_bin = prefix / "bin"
    default_scripts = tmp_path / "default scripts"
    _fake_executable(prefix_bin, "4.5.6")
    python = _write_executable(
        tools_dir / "python3",
        f"#!/bin/sh\nif [ \"$1\" = \"-c\" ]; then echo '{default_scripts}'; fi\n",
    )
    _write_executable(
        tools_dir / "pip3",
        f"#!{python}\nif [ \"$2\" = \"--help\" ]; then echo --user; fi\n",
    )

    result = _run_installer_method(
        tmp_path,
        method="pip",
        path_dirs=[tools_dir, prefix_bin, Path("/usr/bin"), Path("/bin")],
        extra_env={"PIP_PREFIX": str(prefix)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(prefix_bin / "kollab") in result.stdout
    assert "4.5.6" in result.stdout


def test_windows_console_launcher_is_verified(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    installed_dir = tmp_path / "Python Scripts"
    _write_executable(tools_dir / "uname", "#!/bin/sh\necho MINGW64_NT-10.0\n")
    _write_executable(
        installed_dir / "kollab.exe", "#!/bin/sh\necho 'kollab 4.5.6'\n"
    )

    result = _run_verification(
        tmp_path,
        installed_dir=installed_dir,
        path_dirs=[tools_dir, installed_dir, Path("/usr/bin"), Path("/bin")],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(installed_dir / "kollab.exe") in result.stdout


def test_windows_uv_bin_directory_is_converted_for_bash(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    installed_dir = tmp_path / "uv bin"
    _write_executable(tools_dir / "uname", "#!/bin/sh\necho MINGW64_NT-10.0\n")
    _write_executable(
        tools_dir / "cygpath",
        '#!/bin/sh\n[ "$1" = -u ] && echo "$POSIX_BIN"\n',
    )
    _write_executable(
        tools_dir / "uv",
        '#!/bin/sh\nif [ "$1" = tool ] && [ "$2" = dir ]; then echo "$WINDOWS_BIN"; fi\nexit 0\n',
    )
    _write_executable(
        installed_dir / "kollab.exe", "#!/bin/sh\necho 'kollab 4.5.6'\n"
    )

    result = _run_installer_method(
        tmp_path,
        method="uv",
        path_dirs=[tools_dir, installed_dir, Path("/usr/bin"), Path("/bin")],
        extra_env={
            "WINDOWS_BIN": r"C:\Users\fixture\AppData\Local\bin",
            "POSIX_BIN": str(installed_dir),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(installed_dir / "kollab.exe") in result.stdout


def test_windows_pipx_bin_directory_is_converted_for_bash(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    installed_dir = tmp_path / "pipx bin"
    _write_executable(tools_dir / "uname", "#!/bin/sh\necho MINGW64_NT-10.0\n")
    _write_executable(
        tools_dir / "cygpath",
        '#!/bin/sh\n[ "$1" = -u ] && echo "$POSIX_BIN"\n',
    )
    _write_executable(
        tools_dir / "pipx",
        '#!/bin/sh\nif [ "$1" = environment ]; then echo "$WINDOWS_BIN"; fi\nexit 0\n',
    )
    _write_executable(
        installed_dir / "kollab.exe", "#!/bin/sh\necho 'kollab 4.5.6'\n"
    )

    result = _run_installer_method(
        tmp_path,
        method="pipx",
        path_dirs=[tools_dir, installed_dir, Path("/usr/bin"), Path("/bin")],
        extra_env={
            "WINDOWS_BIN": r"C:\Users\fixture\pipx\bin",
            "POSIX_BIN": str(installed_dir),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(installed_dir / "kollab.exe") in result.stdout


def test_windows_pip_prefix_uses_scripts_directory(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    prefix = tmp_path / "custom prefix"
    scripts_dir = prefix / "Scripts"
    _write_executable(tools_dir / "uname", "#!/bin/sh\necho MINGW64_NT-10.0\n")
    _write_executable(
        tools_dir / "pip3",
        '#!/bin/sh\nif [ "$1" = install ] && [ "$2" = --help ]; then echo --user; fi\nexit 0\n',
    )
    _write_executable(
        scripts_dir / "kollab.exe", "#!/bin/sh\necho 'kollab 4.5.6'\n"
    )

    result = _run_installer_method(
        tmp_path,
        method="pip",
        path_dirs=[tools_dir, scripts_dir, Path("/usr/bin"), Path("/bin")],
        extra_env={"PIP_PREFIX": str(prefix)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(scripts_dir / "kollab.exe") in result.stdout


@pytest.mark.parametrize("user_setting", ["environment", "config"])
def test_windows_pip_user_install_uses_user_scripts_directory(
    tmp_path: Path, user_setting: str
) -> None:
    tools_dir = tmp_path / "tools"
    python_313_dir = tmp_path / "Python 3.13 Scripts"
    python_314_dir = tmp_path / "Python 3.14"
    scripts_dir = tmp_path / "Python User Scripts"
    wrong_scripts_dir = tmp_path / "Wrong Python User Scripts"
    _write_executable(tools_dir / "uname", "#!/bin/sh\necho MINGW64_NT-10.0\n")
    _write_executable(
        python_313_dir / "python3",
        '#!/bin/sh\n[ "$1" = -c ] && echo "$WRONG_USER_SCRIPTS"\n',
    )
    pip_python = _write_executable(
        python_314_dir / "python.exe",
        '''#!/bin/sh
if [ "$1" = -c ]; then
    echo "$USER_SCRIPTS"
elif [ "$2" = config ] && [ "$3" = get ] && [ "$4" = install.user ]; then
    echo true
elif [ "$2" = install ] && [ "$3" = --help ]; then
    echo --user
fi
exit 0
''',
    )
    pip_script = "#!/bin/sh\n" + f'''\'\'\'exec\' "{pip_python}" "$0" "$@"
\' \'\'\'
'''
    _write_executable(tools_dir / "pip3", pip_script)
    _write_executable(
        scripts_dir / "kollab.exe", "#!/bin/sh\necho 'kollab 4.5.6'\n"
    )

    extra_env = {
        "USER_SCRIPTS": str(scripts_dir),
        "WRONG_USER_SCRIPTS": str(wrong_scripts_dir),
    }
    if user_setting == "environment":
        extra_env["PIP_USER"] = "1"

    result = _run_installer_method(
        tmp_path,
        method="pip",
        path_dirs=[tools_dir, python_313_dir, scripts_dir, Path("/usr/bin"), Path("/bin")],
        extra_env=extra_env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(scripts_dir / "kollab.exe") in result.stdout


def test_windows_pip_exe_uses_embedded_interpreter_for_user_scripts(
    tmp_path: Path,
) -> None:
    tools_dir = tmp_path / "tools"
    python_313_dir = tmp_path / "Python 3.13 Scripts"
    python_314_dir = tmp_path / "Python 3.14"
    scripts_dir = tmp_path / "Python User Scripts"
    _write_executable(tools_dir / "uname", "#!/bin/sh\necho MINGW64_NT-10.0\n")
    _write_executable(
        python_313_dir / "python",
        '#!/bin/sh\n[ "$1" = -c ] && echo "$WRONG_USER_SCRIPTS"\n',
    )
    pip_python = _write_executable(
        python_314_dir / "python.exe",
        '#!/bin/sh\n[ "$1" = -c ] && echo "$USER_SCRIPTS"\n',
    )
    pip_launcher = tmp_path / "Python 3.14 Scripts" / "pip3.exe"
    pip_launcher.parent.mkdir(parents=True)
    pip_launcher.write_bytes(
        b"MZ fake console launcher\n#! \""
        + str(pip_python).encode()
        + b"\"\nPK fake archive"
    )
    pip_launcher.chmod(0o755)

    result = _run_pip_scripts_dir(
        tmp_path,
        pip_executable=pip_launcher,
        path_dirs=[tools_dir, python_313_dir, Path("/usr/bin"), Path("/bin")],
        user_scripts=scripts_dir,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == str(scripts_dir)


def test_timeout_kills_a_version_command_that_ignores_term(tmp_path: Path) -> None:
    functions = tmp_path / "installer-functions.sh"
    functions.write_text(
        INSTALL_SCRIPT.read_text().split('\nmain "$@"')[0] + "\n",
        encoding="utf-8",
    )
    installed_dir = tmp_path / "install-bin"
    _write_executable(
        installed_dir / "kollab",
        "#!/bin/sh\ntrap '' TERM\nwhile :; do sleep 0.05; done\n",
    )
    env = os.environ.copy()
    env.update({"HOME": str(tmp_path), "PATH": f"{installed_dir}:/usr/bin:/bin"})
    process = subprocess.Popen(
        [
            "/bin/bash",
            "-c",
            'source "$1"; INSTALLED_DIR="$2"; VERSION_CHECK_TIMEOUT_SECONDS=1; verify_install',
            "installer-test",
            str(functions),
            str(installed_dir),
        ],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    try:
        stdout, _ = process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        pytest.fail("version verification hung after its one second timeout")

    assert process.returncode != 0
    assert "timed out" in stdout
