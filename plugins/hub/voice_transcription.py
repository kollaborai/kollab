"""Standalone owner for Telegram's untrusted CLI process lifetime.

Execute this stdlib-only program with the installed Python 3 interpreter, rather
than restarting Kollab (which may be a single-file binary). Keeping the source
as a string also makes it available in that binary without a source-file path.
"""

SUPERVISOR = r'''
import ctypes
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

stopping = False
timed_out = False
root_pid = None
root_status = None


def stop(_signum, _frame):
    global stopping
    stopping = True


def drain(children_file, deadline):
    """ECHILD, not pipe EOF, proves that this run has no remaining writers."""
    global root_status, stopping, timed_out
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid:
            if pid == root_pid:
                root_status = os.waitstatus_to_exitcode(status)
            continue
        if time.monotonic() >= deadline:
            stopping = timed_out = True
        if stopping:
            # Only our direct children: killing each parent reparents all its
            # surviving descendants to this subreaper, even after setsid or a
            # double fork. Reap and repeat until the kernel reports ECHILD.
            # We do not reap between reading a child PID and signalling it, so
            # an exited child's PID cannot be reused underneath this kill.
            for child in children_file.read_text().split():
                try:
                    os.kill(int(child), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        time.sleep(0.01)


def main():
    global root_pid, stopping
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    os.umask(0o077)
    # Set child ownership BEFORE creating any child. Do not change the app's
    # own reaping policy, or depend on finding a descendant before it exits.
    if sys.platform != "linux" or ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0):
        return 125
    children_file = Path(f"/proc/self/task/{os.getpid()}/children")
    children_file.read_text()  # Verify the cancellation mechanism before spawn.
    audio = sys.stdin.buffer.read()
    if stopping:
        return 1
    run_dir = tempfile.mkdtemp(prefix="kollab-telegram-voice-", dir=sys.argv[1])
    deadline = time.monotonic() + 60
    try:
        audio_path = Path(run_dir) / ("voice" + sys.argv[2])
        audio_path.write_bytes(audio)
        # CLI descendants never inherit the app-facing pipes. Their stdout is
        # private run data too, and cannot hold communicate() open indefinitely.
        with tempfile.TemporaryFile(dir=run_dir) as output:
            proc = subprocess.Popen(
                ["whisper", str(audio_path), "--model", "base", "--language", "en",
                 "--output_format", "txt", "--output_dir", run_dir],
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            root_pid = proc.pid
            drain(children_file, deadline)
            proc.returncode = root_status
            if timed_out:
                return 124
            if stopping or root_status != 0:
                return 1
            transcript_path = audio_path.with_suffix(".txt")
            transcript = transcript_path.read_text().strip() if transcript_path.exists() else ""
            if not transcript:
                output.seek(0)
                for line in output.read().decode("utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line and not line.startswith(("Detecting", "[")):
                        transcript = line
                        break
            # Read only after ALL descendants finish. A late writer can be the
            # process that produces the successful transcript.
            sys.stdout.write(transcript or "[voice message - whisper returned empty]")
            return 0
    finally:
        stopping = True
        # If an OS error prevents proof of ECHILD, leave the directory private
        # rather than deleting live writers' files. The caller sees failure.
        drain(children_file, deadline)
        shutil.rmtree(run_dir)


try:
    result = main()
except BaseException:
    # CLI diagnostics and exception strings can contain private data. Only the
    # fixed exit status crosses this boundary on failure.
    result = 1
sys.exit(result)
'''
