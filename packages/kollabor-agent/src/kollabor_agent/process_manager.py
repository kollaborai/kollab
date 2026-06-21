"""Process lifecycle management for agent subprocesses.

Strategy pattern for spawn backends. Default: SubprocessStrategy.
Extension points: DockerStrategy, SSHStrategy (future phases).

Includes circuit breaker (crash-loop prevention) and lightweight
resource tracking (RSS, uptime, restart count).
"""

import asyncio


def _get_loop():
    """Return the running event loop, or create one if none is running."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()

import logging
import os
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class ProcessState(Enum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"
    CIRCUIT_OPEN = "circuit_open"


@dataclass
class ResourceSnapshot:
    """Point-in-time resource usage for one managed process."""

    timestamp: float = field(default_factory=time.time)
    rss_bytes: int = 0  # resident set size
    cpu_percent: float = 0.0  # not sampled continuously -- on-demand
    stdout_lines: int = 0  # total lines captured

    @property
    def rss_mb(self) -> float:
        return self.rss_bytes / (1024 * 1024)


@dataclass
class ManagedProcess:
    """Everything the ProcessManager knows about one agent process."""

    name: str
    pid: int = 0
    state: ProcessState = ProcessState.PENDING
    started_at: float = 0.0
    stopped_at: float = 0.0
    restart_count: int = 0
    last_crash: float = 0.0
    exit_code: Optional[int] = None
    strategy_name: str = ""
    resources: ResourceSnapshot = field(default_factory=ResourceSnapshot)
    meta: Dict[str, Any] = field(default_factory=dict)

    # Set by strategy -- opaque handle the strategy needs to manage its
    # process (Popen for subprocess, container id for docker, etc).
    _handle: Any = field(default=None, repr=False)

    # Ring buffer for stdout capture
    _ring_buffer: Optional["RingBuffer"] = field(default=None, repr=False)

    @property
    def uptime(self) -> float:
        if self.started_at == 0:
            return 0.0
        end = self.stopped_at if self.stopped_at else time.time()
        return end - self.started_at

    @property
    def uptime_str(self) -> str:
        s = int(self.uptime)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h{m:02d}m{s:02d}s"
        return f"{m}m{s:02d}s"

    @property
    def is_alive(self) -> bool:
        return self.state in (ProcessState.STARTING, ProcessState.RUNNING)


class RingBuffer:
    """Thread-safe ring buffer for stdout capture.

    Identical to the one in plugins/agent_orchestrator/ring_buffer.py
    but lives here so the package is self-contained.
    """

    def __init__(self, maxlen: int = 2000):
        self._buf: deque[str] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._total: int = 0  # total lines ever appended

    def append(self, line: str) -> None:
        with self._lock:
            self._buf.append(line)
            self._total += 1

    def get_last(self, n: int) -> List[str]:
        with self._lock:
            if n >= len(self._buf):
                return list(self._buf)
            return list(self._buf)[-n:]

    def get_all(self) -> List[str]:
        with self._lock:
            return list(self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    @property
    def total_lines(self) -> int:
        return self._total

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


@dataclass
class CircuitBreakerConfig:
    """When an agent crashes N times in M seconds, stop respawning."""

    max_failures: int = 3
    window_seconds: float = 120.0
    cooldown_seconds: float = 300.0  # how long circuit stays open


class CircuitBreaker:
    """Per-process crash loop detector."""

    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        self.config = config or CircuitBreakerConfig()
        # name -> list of crash timestamps
        self._failures: Dict[str, deque] = {}
        # name -> when the circuit was opened
        self._opened_at: Dict[str, float] = {}

    def record_failure(self, name: str) -> bool:
        """Record a crash. Returns True if circuit is now OPEN (stop respawning)."""
        now = time.time()

        if name not in self._failures:
            self._failures[name] = deque()

        q = self._failures[name]
        q.append(now)

        # Evict old failures outside the window
        cutoff = now - self.config.window_seconds
        while q and q[0] < cutoff:
            q.popleft()

        if len(q) >= self.config.max_failures:
            self._opened_at[name] = now
            logger.warning(
                f"circuit breaker OPEN for {name}: "
                f"{len(q)} crashes in {self.config.window_seconds}s"
            )
            return True

        return False

    def is_open(self, name: str) -> bool:
        """Check if respawning is blocked for this process."""
        opened = self._opened_at.get(name)
        if opened is None:
            return False
        if time.time() - opened > self.config.cooldown_seconds:
            # Cooldown expired -- close circuit, clear history
            del self._opened_at[name]
            self._failures.pop(name, None)
            logger.info(f"circuit breaker CLOSED for {name} (cooldown expired)")
            return False
        return True

    def reset(self, name: str) -> None:
        """Manually close the circuit (e.g. after human ack)."""
        self._opened_at.pop(name, None)
        self._failures.pop(name, None)

    def get_failure_count(self, name: str) -> int:
        q = self._failures.get(name)
        return len(q) if q else 0


# ---------------------------------------------------------------------------
# SpawnStrategy protocol
# ---------------------------------------------------------------------------


@dataclass
class SpawnRequest:
    """Declarative description of what to spawn.

    The ProcessManager figures out HOW based on the active strategy.
    Callers describe WHAT they want.
    """

    name: str
    cmd: List[str]
    cwd: str = ""
    env: Optional[Dict[str, str]] = None
    ring_buffer_size: int = 2000

    # Optional: the strategy can use these for more advanced setups
    image: str = ""  # docker image (DockerStrategy)
    ssh_host: str = ""  # remote host (SSHStrategy)


@dataclass
class SpawnResult:
    """What the strategy hands back after spawning."""

    success: bool
    pid: int = 0
    handle: Any = None  # opaque -- strategy-specific
    ring_buffer: Optional[RingBuffer] = None
    error: str = ""


class SpawnStrategy(ABC):
    """Backend for process lifecycle.

    Implement this to add new spawn mechanisms (docker, ssh, etc).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name (e.g. 'subprocess', 'docker')."""
        ...

    @abstractmethod
    async def spawn(self, request: SpawnRequest) -> SpawnResult:
        """Start a process. Returns a SpawnResult."""
        ...

    @abstractmethod
    async def kill(self, handle: Any, graceful_timeout: float = 5.0) -> bool:
        """Stop a process. Returns True if successfully terminated."""
        ...

    @abstractmethod
    def is_alive(self, handle: Any) -> bool:
        """Check if the process is still running."""
        ...

    @abstractmethod
    def get_exit_code(self, handle: Any) -> Optional[int]:
        """Get exit code, or None if still running."""
        ...

    async def send_stdin(self, handle: Any, data: str) -> bool:
        """Write data to the process stdin. Override if supported."""
        return False

    def get_resource_snapshot(self, handle: Any) -> ResourceSnapshot:
        """Get current resource usage. Override for richer data."""
        return ResourceSnapshot()

    async def promote(
        self, handle: Any, target_strategy: "SpawnStrategy"
    ) -> SpawnResult:
        """Hot-swap: migrate a running process to a different strategy.

        Default: not supported. Override for strategies that can migrate
        (e.g. subprocess -> docker by committing fs state).
        """
        raise NotImplementedError(
            f"{self.name} does not support promotion to {target_strategy.name}"
        )


# ---------------------------------------------------------------------------
# SubprocessStrategy -- the default
# ---------------------------------------------------------------------------


class SubprocessStrategy(SpawnStrategy):
    """Spawn agents as local subprocesses with piped stdio."""

    @property
    def name(self) -> str:
        return "subprocess"

    async def spawn(self, request: SpawnRequest) -> SpawnResult:
        cwd = request.cwd or str(Path.cwd())
        env = request.env
        if env is None:
            env = os.environ.copy()

        try:
            proc = subprocess.Popen(
                request.cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                env=env,
            )
        except Exception as e:
            return SpawnResult(success=False, error=str(e))

        ring_buf = RingBuffer(maxlen=request.ring_buffer_size)

        # Daemon thread pumps stdout into ring buffer
        pump = threading.Thread(
            target=self._pump_stdout,
            args=(proc, ring_buf, request.name),
            daemon=True,
            name=f"pump-{request.name}",
        )
        pump.start()

        return SpawnResult(
            success=True,
            pid=proc.pid,
            handle=proc,
            ring_buffer=ring_buf,
        )

    async def kill(self, handle: Any, graceful_timeout: float = 5.0) -> bool:
        proc: subprocess.Popen = handle
        if proc.poll() is not None:
            self._close_fds(proc)
            return True  # already dead

        loop = _get_loop()
        try:
            proc.terminate()
            try:
                await loop.run_in_executor(
                    None, lambda: proc.wait(timeout=graceful_timeout)
                )
            except subprocess.TimeoutExpired:
                proc.kill()
                await loop.run_in_executor(None, lambda: proc.wait(timeout=2))
            return True
        except Exception as e:
            logger.error(f"kill failed (pid={proc.pid}): {e}")
            return False
        finally:
            self._close_fds(proc)

    @staticmethod
    def _close_fds(proc: subprocess.Popen) -> None:
        """Close stdin/stdout file descriptors to prevent fd leaks."""
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe:
                try:
                    pipe.close()
                except Exception:
                    pass

    def is_alive(self, handle: Any) -> bool:
        proc: subprocess.Popen = handle
        return proc.poll() is None

    def get_exit_code(self, handle: Any) -> Optional[int]:
        proc: subprocess.Popen = handle
        return proc.poll()

    async def send_stdin(self, handle: Any, data: str) -> bool:
        proc: subprocess.Popen = handle
        if proc.stdin is None or proc.poll() is not None:
            return False

        loop = asyncio.get_running_loop()

        def _write():
            try:
                proc.stdin.write((data + "\n").encode("utf-8"))
                proc.stdin.flush()
                return True
            except (BrokenPipeError, OSError) as e:
                logger.error(f"stdin write failed (pid={proc.pid}): {e}")
                return False

        return await loop.run_in_executor(None, _write)

    def get_resource_snapshot(self, handle: Any) -> ResourceSnapshot:
        proc: subprocess.Popen = handle
        pid = proc.pid
        snap = ResourceSnapshot()
        try:
            snap.rss_bytes = self._get_rss(pid)
        except Exception:
            pass
        return snap

    @staticmethod
    def _get_rss(pid: int) -> int:
        """Best-effort RSS read without psutil."""
        # Linux
        stat_path = Path(f"/proc/{pid}/status")
        if stat_path.exists():
            try:
                for line in stat_path.read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        # VmRSS:    12345 kB
                        parts = line.split()
                        return int(parts[1]) * 1024
            except Exception:
                pass

        # macOS fallback
        try:
            out = subprocess.check_output(
                ["ps", "-o", "rss=", "-p", str(pid)],
                timeout=2,
            )
            return int(out.strip()) * 1024  # ps reports kB on darwin
        except Exception:
            return 0

    @staticmethod
    def _pump_stdout(proc: subprocess.Popen, ring_buf: RingBuffer, label: str) -> None:
        """Read proc.stdout line-by-line into ring buffer.

        Runs in a daemon thread until stdout closes.
        """
        stdout = proc.stdout
        if stdout is None:
            return
        try:
            for raw in iter(stdout.readline, b""):
                try:
                    line = raw.decode("utf-8", errors="replace").rstrip("\n")
                except Exception:
                    line = repr(raw)
                ring_buf.append(line)
        except Exception as e:
            logger.debug(f"[pump] {label} ended: {e}")
        finally:
            logger.debug(f"[pump] {label} stdout closed")


# ---------------------------------------------------------------------------
# ProcessManager
# ---------------------------------------------------------------------------


class ProcessManager:
    """Manages agent process lifecycles through pluggable strategies.

    Responsibilities:
      - spawn / kill / send_stdin / capture_output
      - circuit breaker (crash-loop detection)
      - resource tracking (RSS, uptime, restart count)
      - strategy hot-swap (future: subprocess -> docker)
      - process pool queries (find by name, pattern, state)

    Usage:
        pm = ProcessManager()  # defaults to SubprocessStrategy
        req = SpawnRequest(name="coder-1", cmd=["python3", "main.py", ...])
        mp = await pm.spawn(req)
        output = pm.capture_output("coder-1", lines=50)
        await pm.kill("coder-1")
    """

    def __init__(
        self,
        strategy: Optional[SpawnStrategy] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
        on_crash: Optional[Callable[[str, ManagedProcess], None]] = None,
    ):
        self._strategy = strategy or SubprocessStrategy()
        self._circuit = CircuitBreaker(circuit_breaker_config)
        self._processes: Dict[str, ManagedProcess] = {}
        self._lock = asyncio.Lock()
        self._on_crash = on_crash  # callback when a process crashes

    # -- properties --

    @property
    def strategy(self) -> SpawnStrategy:
        return self._strategy

    @strategy.setter
    def strategy(self, new: SpawnStrategy) -> None:
        """Change the default strategy for future spawns.

        Does NOT affect already-running processes. Each ManagedProcess
        remembers which strategy spawned it.
        """
        logger.info(f"strategy changed: {self._strategy.name} -> {new.name}")
        self._strategy = new

    # -- spawn --

    async def spawn(
        self,
        request: SpawnRequest,
        strategy: Optional[SpawnStrategy] = None,
    ) -> Optional[ManagedProcess]:
        """Spawn a new managed process.

        Args:
            request: What to spawn (name, cmd, env, etc).
            strategy: Override strategy for this spawn only.

        Returns:
            ManagedProcess on success, None if blocked by circuit breaker
            or spawn failure.
        """
        strat = strategy or self._strategy

        async with self._lock:
            # Circuit breaker check
            if self._circuit.is_open(request.name):
                logger.warning(
                    f"spawn blocked: circuit breaker open for {request.name}"
                )
                return None

            # Kill existing process with same name if alive
            existing = self._processes.get(request.name)
            if existing and existing.is_alive:
                logger.warning(f"killing existing {request.name} before respawn")
                await self._kill_process(existing)

            # Spawn while holding lock to prevent concurrent same and name spawns
            result = await strat.spawn(request)

            if not result.success:
                logger.error(f"spawn failed for {request.name}: {result.error}")
                return None

            mp = ManagedProcess(
                name=request.name,
                pid=result.pid,
                state=ProcessState.RUNNING,
                started_at=time.time(),
                strategy_name=strat.name,
                _handle=result.handle,
                _ring_buffer=result.ring_buffer,
            )

            self._processes[request.name] = mp

        logger.info(f"spawned {request.name} via {strat.name} (pid={result.pid})")
        return mp

    # -- kill --

    async def kill(self, name: str, graceful_timeout: float = 5.0) -> bool:
        """Kill a managed process by name.

        Returns True if terminated (or already dead).
        """
        async with self._lock:
            mp = self._processes.get(name)
            if mp is None:
                return False

        logger.info(
            f"initiating kill for {name} (graceful_timeout={graceful_timeout}s)"
        )
        success = await self._kill_process(mp, graceful_timeout)

        async with self._lock:
            mp.state = ProcessState.STOPPED
            mp.stopped_at = time.time()
            mp.exit_code = self._get_strategy(mp).get_exit_code(mp._handle)

        if success:
            logger.info(f"successfully terminated {name}")
        else:
            logger.error(f"failed to terminate {name} after timeout")

        return success

    async def kill_all(self, graceful_timeout: float = 5.0) -> int:
        """Kill every managed process. Returns count killed."""
        names = list(self._processes.keys())
        killed = 0
        for name in names:
            if await self.kill(name, graceful_timeout):
                killed += 1
        return killed

    # -- stdin --

    async def send_stdin(self, name: str, data: str) -> bool:
        """Write data to a process's stdin."""
        mp = self._processes.get(name)
        if mp is None or not mp.is_alive:
            return False
        strat = self._get_strategy(mp)
        return await strat.send_stdin(mp._handle, data)

    # -- output capture --

    def capture_output(self, name: str, lines: int = 50) -> str:
        """Get last N lines from a process's ring buffer."""
        mp = self._processes.get(name)
        if mp is None or mp._ring_buffer is None:
            return ""
        return "\n".join(mp._ring_buffer.get_last(lines))

    def capture_output_lines(self, name: str, lines: int = 50) -> List[str]:
        """Get last N lines as a list."""
        mp = self._processes.get(name)
        if mp is None or mp._ring_buffer is None:
            return []
        return mp._ring_buffer.get_last(lines)

    # -- queries --

    def get(self, name: str) -> Optional[ManagedProcess]:
        """Get a managed process by name."""
        return self._processes.get(name)

    def list_all(self) -> List[ManagedProcess]:
        """All managed processes (alive + dead)."""
        return list(self._processes.values())

    def list_alive(self) -> List[ManagedProcess]:
        """Only processes currently running."""
        self._refresh_states()
        return [mp for mp in self._processes.values() if mp.is_alive]

    def find(self, pattern: str) -> List[ManagedProcess]:
        """Find processes by glob pattern on name."""
        import fnmatch

        return [
            mp for mp in self._processes.values() if fnmatch.fnmatch(mp.name, pattern)
        ]

    def count_alive(self) -> int:
        self._refresh_states()
        return sum(1 for mp in self._processes.values() if mp.is_alive)

    # -- resource tracking --

    def snapshot_resources(self, name: str) -> Optional[ResourceSnapshot]:
        """Take a fresh resource snapshot for one process."""
        mp = self._processes.get(name)
        if mp is None or not mp.is_alive:
            return None
        strat = self._get_strategy(mp)
        snap = strat.get_resource_snapshot(mp._handle)
        if mp._ring_buffer:
            snap.stdout_lines = mp._ring_buffer.total_lines
        mp.resources = snap
        return snap

    def snapshot_all_resources(self) -> Dict[str, ResourceSnapshot]:
        """Snapshot resources for all alive processes."""
        out: Dict[str, ResourceSnapshot] = {}
        for name, mp in self._processes.items():
            if mp.is_alive:
                snap = self.snapshot_resources(name)
                if snap:
                    out[name] = snap
        return out

    # -- circuit breaker --

    def circuit_reset(self, name: str) -> None:
        """Manually reset the circuit breaker for a process."""
        self._circuit.reset(name)
        mp = self._processes.get(name)
        if mp and mp.state == ProcessState.CIRCUIT_OPEN:
            mp.state = ProcessState.STOPPED
        logger.info(f"circuit breaker manually reset for {name}")

    def circuit_status(self, name: str) -> Dict[str, Any]:
        """Get circuit breaker state for a process."""
        return {
            "is_open": self._circuit.is_open(name),
            "failure_count": self._circuit.get_failure_count(name),
            "config": {
                "max_failures": self._circuit.config.max_failures,
                "window_seconds": self._circuit.config.window_seconds,
                "cooldown_seconds": self._circuit.config.cooldown_seconds,
            },
        }

    # -- cleanup --

    async def cleanup_dead(self) -> int:
        """Remove dead processes from tracking. Returns count removed."""
        self._refresh_states()
        dead = [
            name
            for name, mp in self._processes.items()
            if mp.state in (ProcessState.STOPPED, ProcessState.CRASHED)
        ]
        for name in dead:
            del self._processes[name]
        return len(dead)

    async def shutdown(self) -> None:
        """Kill all processes and clear state. Call on app exit."""
        logger.info("initiating ProcessManager shutdown sequence")
        await self.kill_all()
        self._processes.clear()
        logger.info("ProcessManager shutdown complete")

    # -- internals --

    def _get_strategy(self, mp: ManagedProcess) -> SpawnStrategy:
        """Resolve which strategy owns this process.

        Right now we only have one strategy at a time. When hot-swap
        lands, each ManagedProcess will carry a reference to its
        specific strategy instance.
        """
        return self._strategy

    async def _kill_process(
        self, mp: ManagedProcess, graceful_timeout: float = 5.0
    ) -> bool:
        strat = self._get_strategy(mp)
        return await strat.kill(mp._handle, graceful_timeout)

    def _refresh_states(self) -> None:
        """Poll each process and update state if it died."""
        for mp in self._processes.values():
            if mp.state not in (ProcessState.STARTING, ProcessState.RUNNING):
                continue
            strat = self._get_strategy(mp)
            if not strat.is_alive(mp._handle):
                exit_code = strat.get_exit_code(mp._handle)
                mp.exit_code = exit_code
                mp.stopped_at = time.time()

                if exit_code is not None and exit_code != 0:
                    logger.error(f"process {mp.name} crashed (exit_code={exit_code})")
                    mp.state = ProcessState.CRASHED
                    mp.last_crash = time.time()
                    mp.restart_count += 1

                    opened = self._circuit.record_failure(mp.name)
                    if opened:
                        logger.warning(f"circuit breaker OPEN for {mp.name}")
                        mp.state = ProcessState.CIRCUIT_OPEN

                    if self._on_crash:
                        try:
                            self._on_crash(mp.name, mp)
                        except Exception:
                            pass
                else:
                    mp.stopped_at = time.time()
                    mp.state = ProcessState.STOPPED
