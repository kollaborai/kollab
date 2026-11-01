#!/usr/bin/env python3
"""Build the single-file kollab binary for this machine and check that it runs.

    python scripts/build_binary.py                    # builds the wheels from this checkout
    python scripts/build_binary.py --wheels dist      # or uses wheels already built
    python scripts/build_binary.py --expect-version 0.15.1
    python scripts/build_binary.py --frontend-dist /tmp/kollab-ui

--frontend-dist bundles an already built UI without rebuilding it. Pass the same
directory to scripts/relay/build_service.py to package the same browser assets.
With --wheels, it checks that those wheels already contain that exact UI.

The binary is a pex scie: one executable holding a portable CPython and every
wheel kollab needs, so it runs with no Python installed. It lands in --out as
kollab-<os>-<arch> (macos-aarch64, linux-x86_64, windows-x86_64.exe, ...) beside
a .sha256 file, which install.sh and /upgrade check before installing it.
On Windows it is the desktop installer's runtime and no release asset by itself:
see docs/specs/windows-desktop.md for what that runtime cannot do yet.

Then the binary must report embedded build provenance and the expected
version, import kollab's packages, and verify an HTTPS certificate with its own
Python. CI runs this on each platform (.github/workflows/binaries.yml).

    python scripts/build_binary.py --agent-smoke dist/bin/kollab-windows-x86_64.exe

--agent-smoke builds nothing: it runs the check a Windows binary must pass before
a desktop installer may carry it. A detached agent starts, shows online in
`--hub status` and stops again (native daemon start and local IPC, no model turn).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# pex 2.98.3 and later carry scie-jump 1.13.0, which cannot read back a Windows
# scie's configure env file ("expected a borrowed string", #268); 2.98.2 is the
# last on 1.12.0. Everywhere else stay on the newer pex: 2.98.3 made --venv scies
# survive a deleted PEX_ROOT cache (pex#3226).
PEX = "pex==2.98.2" if platform.system() == "Windows" else "pex==2.103.4"
# The CPython the binary carries: a Python Standalone Builds release and version.
# It, not the kollab file, is what reads the Keychain, and macOS knows it by its
# code hash (it is ad hoc signed): changing either makes every Mac ask once again.
# scripts/desktop_release.py lays out the same runtime as RUNTIME_PYTHON.
PBS_RELEASE = "20261009"
PYTHON = "3.14.8"
# pex runs on the same minor version, since floor_platform reads its wheel tags.
UVX_PEX = ["uvx", "--python", PYTHON.rsplit(".", 1)[0], "--from", PEX]
# The oldest systems the binary runs on: macOS 11, and glibc 2.28 (Debian 10+,
# Ubuntu 20.04+, RHEL 8+). Wheels are picked for these, never for the build
# machine, so a newer CI image cannot raise the floor unnoticed. Windows wheels
# (win_amd64) carry no OS floor; the portable CPython sets it.
FLOORS = {"macosx": (11, 0), "manylinux": (2, 28)}
IMPORTS = "import kollabor.application, kollabor_cli_main, kollabor_engine, kollabor_webui, kollabor_voice, plugins"
TLS_URL = "https://pypi.org/simple/kollab/"
# Workspace members with no wheel of their own: the kollab wheel carries their code.
BUNDLED = ("kollabor_voice-",)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


def kollab_version(wheels: list[Path]) -> str:
    for wheel in wheels:
        match = re.fullmatch(r"kollab-([^-]+)-.+\.whl", wheel.name)
        if match:
            return match.group(1)
    raise SystemExit("no kollab wheel among: " + ", ".join(w.name for w in wheels))


def kollab_build_line(wheels: list[Path]) -> str:
    """Read the identity embedded in the exact wheel that enters the binary."""
    wheel = next(
        (path for path in wheels if path.name.startswith("kollab-")), None
    )
    if wheel is None:
        raise SystemExit(
            "no kollab wheel among: " + ", ".join(w.name for w in wheels)
        )

    try:
        with zipfile.ZipFile(wheel) as archive:
            source = archive.read("kollabor/_build_info.py").decode("utf-8")
        module = ast.parse(source, filename=f"{wheel.name}:kollabor/_build_info.py")
    except (KeyError, UnicodeDecodeError, SyntaxError, zipfile.BadZipFile) as error:
        raise SystemExit(
            f"{wheel.name} has no valid embedded build provenance: {error}"
        ) from error

    values: dict[str, object] = {}
    for statement in module.body:
        if (
            not isinstance(statement, ast.Assign)
            or len(statement.targets) != 1
            or not isinstance(statement.targets[0], ast.Name)
        ):
            continue
        name = statement.targets[0].id
        if name in {"BUILD_BRANCH", "BUILD_SHA"}:
            try:
                values[name] = ast.literal_eval(statement.value)
            except (ValueError, TypeError):
                continue

    branch = values.get("BUILD_BRANCH")
    sha = values.get("BUILD_SHA")
    if not isinstance(branch, str) or not isinstance(sha, str):
        raise SystemExit(f"{wheel.name} is missing branch or full SHA metadata")
    if branch != "unknown" and (
        not branch
        or len(branch) > 255
        or any(
            not character.isprintable() or character.isspace()
            for character in branch
        )
    ):
        raise SystemExit(f"{wheel.name} contains an invalid build branch")
    if sha != "unknown" and not re.fullmatch(
        r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", sha
    ):
        raise SystemExit(f"{wheel.name} contains an invalid full build SHA")

    short_sha = "unknown" if sha == "unknown" else sha[:8]
    return f"Build: {branch} @ {short_sha}"


def verify_frontend(wheels: list[Path], frontend: Path) -> None:
    """Refuse a binary whose WebUI wheel differs from the chosen frontend output."""
    if frontend.is_symlink() or not (frontend / "index.html").is_file():
        raise SystemExit(f"--frontend-dist must contain index.html and not be a symlink: {frontend}")
    expected = {}
    for asset in sorted(frontend.rglob("*")):
        if asset.is_symlink():
            raise SystemExit(f"frontend assets must not be symlinks: {asset}")
        if asset.is_file():
            expected[asset.relative_to(frontend).as_posix()] = asset.read_bytes()
    webui = [wheel for wheel in wheels if wheel.name.startswith("kollabor_webui-")]
    if len(webui) != 1:
        raise SystemExit(f"expected one kollabor-webui wheel, found {len(webui)}")
    prefix = "kollabor_webui/static/assistant/"
    with zipfile.ZipFile(webui[0]) as wheel:
        bundled = {
            name.removeprefix(prefix): wheel.read(name)
            for name in wheel.namelist()
            if name.startswith(prefix) and not name.endswith("/")
        }
    mismatched = sorted(name for name in expected.keys() | bundled.keys() if expected.get(name) != bundled.get(name))
    if mismatched:
        raise SystemExit(f"{webui[0].name} differs from --frontend-dist: {', '.join(mismatched)}")


def platform_name() -> str:
    """This machine as release binaries name it, as kollabor/updates/auto_update.py:platform_name does.

    Windows is named here for the desktop installer's runtime only: /upgrade and
    install.sh still know no Windows binary.
    """
    system = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    machine = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}.get(
        platform.machine().lower()
    )
    if not (system and machine):
        raise SystemExit(f"no kollab binary for {platform.system()} {platform.machine()}")
    return f"{system}-{machine}"


def binary_filename() -> str:
    """kollab-<platform>, with the .exe suffix pex gives a Windows scie."""
    return f"kollab-{platform_name()}" + (".exe" if platform.system() == "Windows" else "")


def floor_platform(path: Path) -> Path:
    """A pex complete platform: this machine's wheel tags, minus any newer than FLOORS."""
    # pip's --platform cannot do this: it never widens manylinux_2_28 to the older tags.
    inspected = json.loads(run([*UVX_PEX, "pex3", "interpreter", "inspect", "--markers", "--tags"],
                               capture_output=True, text=True).stdout)

    def supported(tag: str) -> bool:
        match = re.fullmatch(r"(macosx|manylinux)_(\d+)_(\d+)_\w+", tag.rsplit("-", 1)[1])
        return not match or (int(match[2]), int(match[3])) <= FLOORS[match[1]]

    markers = {**inspected["marker_environment"], "python_full_version": PYTHON, "implementation_version": PYTHON}
    tags = [tag for tag in inspected["compatible_tags"] if supported(tag)]
    path.write_text(json.dumps({"marker_environment": markers, "compatible_tags": tags}))
    return path


def build(wheels: list[Path], out: Path, scratch: Path) -> Path:
    """The scie for this machine, kollab-<platform>, plus the .sha256 pex writes beside it."""
    out.mkdir(parents=True, exist_ok=True)
    binary = out / binary_filename()
    checksum = binary.with_name(binary.name + ".sha256")
    binary.unlink(missing_ok=True)
    checksum.unlink(missing_ok=True)
    # pex reads each wheel argument as a URL first: a Windows path's drive letter
    # becomes the scheme and the wheel is never found (#268). Bare file names have
    # no colon, so pex runs in the wheels' folder. It also prints its output's path
    # relative to that folder, which Windows refuses across drives, so the scie is
    # written there and then moved to out.
    folder = wheels[0].parent
    run(
        [
            *UVX_PEX, "pex",
            *(wheel.name for wheel in wheels),
            "--complete-platform", str(floor_platform(scratch / "platform.json")),
            "--console-script", "kollab",
            "--venv",
            "--scie", "eager",
            "--scie-only",
            "--scie-name-style", "platform-file-suffix",
            "--scie-pbs-release", PBS_RELEASE,
            "--scie-python-version", PYTHON,
            "--scie-pbs-stripped",
            "--scie-hash-alg", "sha256",
            "--output-file", str(folder / "kollab"),
        ],
        cwd=folder,
    )
    for path in (binary, checksum):
        built = folder / path.name
        if not built.is_file():
            raise SystemExit(f"pex wrote no {path.name}; {folder} holds {sorted(p.name for p in folder.iterdir())}")
        shutil.move(built, path)
    return binary


def smoke_env(**extra: str) -> dict[str, str]:
    """This environment minus any binary identity, which would make one kollab act as another."""
    build_env = {"KOLLAB_BUILD_BRANCH", "KOLLAB_BUILD_SHA"}
    env = {key: value for key, value in os.environ.items() if key not in {"SCIE", "SCIE_ARGV0", "PEX"} | build_env}
    env["KOLLAB_NO_KEYRING"] = "1"
    return {**env, **extra}


def smoke(binary: Path, version: str, expected_build_line: str) -> None:
    """The binary starts outside any checkout, reports the version, imports kollab and verifies TLS."""
    env = smoke_env()
    with tempfile.TemporaryDirectory() as cwd:
        shown = subprocess.run(
            [str(binary), "--version"], cwd=cwd, env=env, capture_output=True, text=True, timeout=600
        )
        lines = shown.stdout.rstrip().splitlines()
        # The executable/version line remains last for users and shell consumers.
        if (
            shown.returncode != 0
            or len(lines) != 2
            or lines[0] != expected_build_line
            or lines[-1].split()[-1:] != [version]
        ):
            raise SystemExit(
                f"{binary.name} --version: expected {expected_build_line} before {version}, "
                f"got:\n{shown.stdout}{shown.stderr}"
            )
        checks = f"{IMPORTS}; import urllib.request; urllib.request.urlopen({TLS_URL!r}, timeout=60).close()"
        probe = subprocess.run(
            [str(binary), "-c", checks], cwd=cwd, env={**env, "PEX_INTERPRETER": "1"},
            capture_output=True, text=True, timeout=600,
        )
        if probe.returncode != 0:
            raise SystemExit(f"{binary.name} cannot import kollab or verify HTTPS:\n{probe.stderr[-2000:]}")


AGENT_ONLINE = re.compile(r"^\s+(?P<name>\S+).*\bpid=(?P<pid>\d+)", re.MULTILINE)


def agent_log_errors(home: Path, limit: int = 40) -> str:
    """The error lines of the agent's own logs under ``home``: its stdio goes nowhere, so they say why it failed."""
    lines = []
    for path in sorted(home.glob(".kollab/projects/*/logs/*.log")):
        for line in path.read_text(errors="replace").splitlines():
            if re.search(r"ERROR|CRITICAL|Traceback|Error:|Exception", line):
                lines.append(line[:300])
    if not lines:
        return "agent logs: no error lines"
    return "agent log errors (last %d):\n%s" % (min(limit, len(lines)), "\n".join(lines[-limit:]))


def agent_smoke(binary: Path, timeout: float = 120.0) -> None:
    """A detached agent starts, shows online in `--hub status`, and stops: native daemon start plus local IPC.

    This is what a Windows binary must pass before a desktop installer may carry
    it (docs/specs/windows-desktop.md); it needs no model provider or network.
    The agent runs with a throwaway home and folder, so it joins no real hub.
    """
    # ignore_cleanup_errors: Windows keeps a folder locked while a stopped agent's files close.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        home, project = Path(scratch, "home"), Path(scratch, "project")
        project.mkdir()
        (home / ".kollab").mkdir(parents=True)
        (home / ".kollab" / "config.json").write_text(json.dumps({"application": {"setup_completed": True}}))
        env = smoke_env(HOME=str(home), USERPROFILE=str(home))

        def kollab(*args: str, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.run([str(binary), *args], cwd=project, env=env, text=True, timeout=60, **kwargs)

        # A file, not a pipe: the detached agent may keep its inherited handles open for good.
        log = Path(scratch, "detached.log")
        with log.open("wb") as stream:
            started = kollab("--detached", stdin=subprocess.DEVNULL, stdout=stream, stderr=stream)
        online = None
        status = None
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                status = kollab("--hub", "status", capture_output=True)
                online = AGENT_ONLINE.search(status.stdout)
                if online:
                    break
                time.sleep(2)
            if online:
                # This machine-wide lookup uses endpoint discovery, separately
                # from the project-local status command above.
                where = kollab("--hub", "where", online["name"], capture_output=True)
                endpoint = where.stdout.strip()
                if where.returncode != 0 or not endpoint or not Path(endpoint).exists():
                    raise SystemExit(
                        f"{binary.name} --hub where {online['name']} did not find its live endpoint: "
                        f"exit {where.returncode}; {where.stderr[-1000:]}"
                    )
                print("machine-wide hub discovery found the live agent endpoint")
        finally:
            if online:
                kollab("--hub", "stop", online["name"], capture_output=True)
        if not online:
            shown = status.stdout + status.stderr[-2000:] if status else ""
            raise SystemExit(
                f"{binary.name} --detached started no agent that answers `--hub status` within {timeout:.0f}s\n"
                f"--detached: exit {started.returncode}\n{log.read_text(errors='replace')[-2000:]}\n"
                f"--hub status:\n{shown}\n{agent_log_errors(home)}"
            )
        deadline = time.monotonic() + 30
        while AGENT_ONLINE.search(kollab("--hub", "status", capture_output=True).stdout):
            if time.monotonic() > deadline:
                raise SystemExit(f"{binary.name} --hub stop {online['name']} left the agent online")
            time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--wheels", type=Path, help="directory of built wheels (default: build them here)")
    parser.add_argument("--frontend-dist", type=Path, help="use this built frontend; verify it when using --wheels")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "bin", help="where the binary goes")
    parser.add_argument("--expect-version", help="fail unless the binary is this version (v prefix allowed)")
    parser.add_argument(
        "--agent-smoke", type=Path, metavar="BINARY", help="build nothing: start and stop a detached agent of BINARY"
    )
    args = parser.parse_args()
    if args.agent_smoke:
        agent_smoke(args.agent_smoke.resolve())
        print(f"{args.agent_smoke.name} started a detached agent, saw it online and stopped it")
        return 0
    selected = os.environ.get("KOLLAB_FRONTEND_DIST")
    if args.frontend_dist is None and selected is not None:
        if not selected:
            parser.error("KOLLAB_FRONTEND_DIST must name a built frontend directory")
        args.frontend_dist = Path(selected)

    with tempfile.TemporaryDirectory() as scratch:
        wheel_dir = args.wheels
        if wheel_dir is None:
            wheel_dir = Path(scratch, "wheels")
            env = dict(os.environ)
            if args.frontend_dist is not None:
                env["KOLLAB_FRONTEND_DIST"] = str(args.frontend_dist.absolute())
            run(["uv", "build", "--all-packages", "--wheel", "--out-dir", str(wheel_dir)], cwd=ROOT, env=env)
        wheels = sorted(p for p in wheel_dir.glob("*.whl") if not p.name.startswith(BUNDLED))
        if args.frontend_dist is not None:
            verify_frontend(wheels, args.frontend_dist)
        version = kollab_version(wheels)
        if args.expect_version and args.expect_version.removeprefix("v") != version:
            raise SystemExit(f"the wheels are kollab {version}, not {args.expect_version}")
        expected_build_line = kollab_build_line(wheels)
        binary = build(wheels, args.out.resolve(), Path(scratch))
        smoke(binary, version, expected_build_line)

    digest = binary.with_name(binary.name + ".sha256").read_text().split()[0]
    size = binary.stat().st_size / 1e6
    print(f"built {binary} (kollab {version}, {size:.0f} MB, sha256 {digest[:16]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
