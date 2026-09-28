"""Detached singleton launcher; installs audio support outside the host runtime."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .control import atomic_json, manifest, prepare_root, service_root


def ensure_runtime(root, pins, imports, progress, detail="Preparing voice support"):
    """Install and verify a pinned, isolated runtime; caller serializes setup."""
    signature = hashlib.sha256(json.dumps(pins).encode()).hexdigest()[:16]
    runtime = root / "runtimes" / signature
    python = runtime / "bin/python"
    marker = runtime / "ready.json"
    probe = [
        str(python),
        "-c",
        imports,
    ]
    ready = False
    if marker.exists() and python.exists():
        try:
            if json.loads(marker.read_text()).get("requirements") == pins:
                subprocess.run(probe, check=True, timeout=60)
                ready = True
        except (ValueError, OSError, subprocess.SubprocessError):
            pass  # A marker is not proof of a working environment.
    if not ready:
        progress("installing", detail)
        runtime.parent.mkdir(exist_ok=True)
        uv = shutil.which("uv")
        repair_existing = python.exists()
        if not python.exists():
            interpreter = sys.executable
            if sys.version_info[:2] not in {(3, 12), (3, 13)}:
                if not uv:
                    raise RuntimeError(
                        "Voice support needs Python 3.12/3.13 or uv to prepare its runtime"
                    )
                interpreter = "3.12"
            cmd = (
                [uv, "venv", "--python", interpreter, str(runtime)]
                if uv
                else [sys.executable, "-m", "venv", str(runtime)]
            )
            subprocess.run(cmd, check=True, timeout=180)
        cmd = (
            [uv, "pip", "install", "--python", str(python), *pins]
            if uv
            else [str(python), "-m", "pip", "install", *pins]
        )
        if repair_existing:
            cmd.append("--reinstall" if uv else "--force-reinstall")
        subprocess.run(cmd, check=True, timeout=900)
        subprocess.run(
            [
                str(python),
                "-c",
                imports,
            ],
            check=True,
            timeout=90,
        )
        atomic_json(marker, {"requirements": pins})
    return python


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=service_root())
    args = parser.parse_args()
    root = args.root
    prepare_root(root)
    lock = (root / "service.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return  # The current owner performs setup and owns audio.

    def progress(state, detail):
        atomic_json(
            root / "setup.json",
            {
                "state": state,
                "detail": detail,
                "pid": os.getpid(),
                "updated_at": time.time(),
            },
        )

    try:
        pins = manifest()["runtime"]
        python = ensure_runtime(
            root,
            pins,
            "import faster_whisper, kokoro_onnx, sounddevice, numpy",
            progress,
        )
        progress("starting", "Starting shared voice service")
        # The lock survives exec. No second setup or daemon can race this one.
        os.set_inheritable(lock.fileno(), True)
        os.execv(
            str(python),
            [
                str(python),
                "-m",
                "kollabor_voice.service",
                "--root",
                str(root),
                "--lock-fd",
                str(lock.fileno()),
            ],
        )
    except Exception as exc:
        progress("error", f"Voice setup failed: {exc}. Run /voicemode retry")
        raise


if __name__ == "__main__":
    main()
