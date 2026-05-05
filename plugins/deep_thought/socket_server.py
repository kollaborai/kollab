"""Unix domain socket server for Deep Thought Engine IPC.

Each kollabor instance can run a socket server that child instances
connect back to for streaming their reasoning in real-time.
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def get_socket_dir() -> Path:
    """Get the directory for agent sockets."""
    socket_dir = Path(tempfile.gettempdir()) / "kollabor-deep-thought"
    socket_dir.mkdir(parents=True, exist_ok=True)
    return socket_dir


def get_socket_path(session_id: str) -> Path:
    """Get socket path for a specific session."""
    return get_socket_dir() / f"{session_id}.sock"


class ThoughtServer:
    """Unix domain socket server that receives reasoning from child instances.

    Protocol (newline-delimited JSON):
        -> child sends: {"type": "hello", "instance_id": "...", "methodology": "..."}
        <- server acks: {"type": "ack"}
        -> child sends: {"type": "chunk", "content": "..."}  (streaming)
        -> child sends: {"type": "done", "content": "...", "summary": "..."}
        <- server acks: {"type": "ack"}
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.socket_path = get_socket_path(session_id)
        self._server: Optional[asyncio.AbstractServer] = None
        self._results: Dict[str, Dict[str, Any]] = {}
        self._completion_events: Dict[str, asyncio.Event] = {}
        self._all_done: Optional[asyncio.Event] = None
        self._expected_count: int = 0
        self._completed_count: int = 0
        self._on_chunk: Optional[Callable] = None

    async def start(self, expected_count: int, on_chunk: Optional[Callable] = None):
        """Start the socket server.

        Args:
            expected_count: Number of child instances expected to connect.
            on_chunk: Optional callback for streaming chunks (instance_id, content).
        """
        self._expected_count = expected_count
        self._completed_count = 0
        self._all_done = asyncio.Event()
        self._on_chunk = on_chunk
        self._results.clear()
        self._completion_events.clear()

        # Clean up stale socket
        if self.socket_path.exists():
            self.socket_path.unlink()

        self._server = await asyncio.start_unix_server(
            self._handle_connection, path=str(self.socket_path)
        )
        # Set permissions so child processes can connect
        os.chmod(str(self.socket_path), 0o600)
        logger.info(f"ThoughtServer listening on {self.socket_path}")

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle a single child instance connection."""
        instance_id = None
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break

                try:
                    msg = json.loads(line.decode().strip())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                msg_type = msg.get("type")

                if msg_type == "hello":
                    instance_id = msg.get("instance_id", "unknown")
                    methodology = msg.get("methodology", "unknown")
                    self._results[instance_id] = {
                        "methodology": methodology,
                        "chunks": [],
                        "content": "",
                        "summary": "",
                        "status": "connected",
                    }
                    event = asyncio.Event()
                    self._completion_events[instance_id] = event
                    logger.info(f"Child connected: {instance_id} ({methodology})")
                    # Ack
                    ack = json.dumps({"type": "ack"}) + "\n"
                    writer.write(ack.encode())
                    await writer.drain()

                elif msg_type == "chunk":
                    content = msg.get("content", "")
                    if instance_id and instance_id in self._results:
                        self._results[instance_id]["chunks"].append(content)
                        if self._on_chunk:
                            try:
                                self._on_chunk(instance_id, content)
                            except Exception:
                                pass

                elif msg_type == "done":
                    content = msg.get("content", "")
                    summary = msg.get("summary", "")
                    if instance_id and instance_id in self._results:
                        self._results[instance_id]["content"] = content
                        self._results[instance_id]["summary"] = summary
                        self._results[instance_id]["status"] = "done"
                        logger.info(
                            f"Child done: {instance_id} " f"({len(content)} chars)"
                        )
                    # Ack
                    ack = json.dumps({"type": "ack"}) + "\n"
                    writer.write(ack.encode())
                    await writer.drain()

                    # Mark complete
                    self._completed_count += 1
                    if instance_id in self._completion_events:
                        self._completion_events[instance_id].set()
                    if self._completed_count >= self._expected_count:
                        assert self._all_done is not None
                        self._all_done.set()
                    break

                elif msg_type == "error":
                    error = msg.get("error", "unknown error")
                    if instance_id and instance_id in self._results:
                        self._results[instance_id]["status"] = "error"
                        self._results[instance_id]["error"] = error
                    logger.error(f"Child error: {instance_id}: {error}")
                    self._completed_count += 1
                    if instance_id in self._completion_events:
                        self._completion_events[instance_id].set()
                    if self._completed_count >= self._expected_count:
                        assert self._all_done is not None
                        self._all_done.set()
                    break

        except Exception as e:
            logger.error(f"Connection handler error: {e}")
            if instance_id:
                self._completed_count += 1
                if instance_id in self._results:
                    self._results[instance_id]["status"] = "error"
                if instance_id in self._completion_events:
                    self._completion_events[instance_id].set()
                if self._completed_count >= self._expected_count:
                    assert self._all_done is not None
                    self._all_done.set()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def wait_all(self, timeout: float = 60.0) -> Dict[str, Dict[str, Any]]:
        """Wait for all child instances to complete.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            Dict mapping instance_id to result dict.
        """
        if self._all_done:
            try:
                await asyncio.wait_for(self._all_done.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    f"Timeout waiting for children "
                    f"({self._completed_count}/{self._expected_count} done)"
                )
        return self._results

    async def stop(self):
        """Stop the socket server and clean up."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass
        logger.info("ThoughtServer stopped")


class ThoughtClient:
    """Client that child instances use to report back to the parent.

    Used inside pipe-mode child instances to stream reasoning back
    to the parent's ThoughtServer.
    """

    def __init__(self, socket_path: str, instance_id: str, methodology: str):
        self.socket_path = socket_path
        self.instance_id = instance_id
        self.methodology = methodology
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self) -> bool:
        """Connect to the parent's ThoughtServer."""
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(
                self.socket_path
            )
            # Send hello
            hello = (
                json.dumps(
                    {
                        "type": "hello",
                        "instance_id": self.instance_id,
                        "methodology": self.methodology,
                    }
                )
                + "\n"
            )
            self._writer.write(hello.encode())
            await self._writer.drain()

            # Wait for ack
            ack_line = await asyncio.wait_for(self._reader.readline(), timeout=5.0)
            if ack_line:
                ack = json.loads(ack_line.decode().strip())
                if ack.get("type") == "ack":
                    return True
            return False
        except Exception as e:
            logger.error(f"Failed to connect to parent: {e}")
            return False

    async def send_chunk(self, content: str):
        """Stream a reasoning chunk back to parent."""
        if self._writer:
            msg = json.dumps({"type": "chunk", "content": content}) + "\n"
            self._writer.write(msg.encode())
            await self._writer.drain()

    async def send_done(self, content: str, summary: str = ""):
        """Signal completion with final content."""
        if self._writer:
            msg = (
                json.dumps(
                    {
                        "type": "done",
                        "content": content,
                        "summary": summary,
                    }
                )
                + "\n"
            )
            self._writer.write(msg.encode())
            await self._writer.drain()

            # Wait for ack
            try:
                assert self._reader is not None
                await asyncio.wait_for(self._reader.readline(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass

    async def send_error(self, error: str):
        """Signal an error."""
        if self._writer:
            msg = (
                json.dumps(
                    {
                        "type": "error",
                        "instance_id": self.instance_id,
                        "error": error,
                    }
                )
                + "\n"
            )
            self._writer.write(msg.encode())
            await self._writer.drain()

    async def close(self):
        """Close the connection."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
