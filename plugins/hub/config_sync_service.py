"""Sealed config sync over the network's secure conversation path.

Primary: every ``POLL_SECONDS`` it looks at the global settings, and for each
accepted device that is online and has not acknowledged the current snapshot
for its current relay session it sends, in order,

  core  config.json overrides + MCP servers (small: a loadout switch is this)
  sync  the file manifest for ``agents/`` and ``skills/``; the device answers
        with a bitmask of what it lacks, then
  put   only those files, in batches that fit one secure request

A new relay session (the device reconnected) or a new snapshot (something
changed) makes a device stale again, so "on accept", "on every change" and "on
reconnect" are one rule. A failed push backs off and retries.

Secondary: ``receive`` accepts these calls only from the device that issued its
join code, and applies them through ``config_sync.Receiver``.

The relay and the directory carry TLS records inside NaCl boxes and never see a
bundle; each bundle is also signed by the primary and sealed to the receiving
device's key. Nothing here logs a value.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import math
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from nacl.signing import SigningKey

from .config_sync import (
    MAX_BATCH_FILES,
    MAX_ZFILE_BYTES,
    Applied,
    ConfigSyncError,
    FileEntry,
    Receiver,
    Snapshot,
    SnapshotBuilder,
    core_blob,
    decode_need,
    kollab_root,
    pack_files,
    pack_json,
    seal,
    sync_body,
)

logger = logging.getLogger(__name__)

POLL_SECONDS = 10.0
# The relay lets a client send 8 frames a second; stay under it so a big batch
# never trips the limit halfway through (which would drop the secure session).
FRAMES_PER_SECOND = 6.0
FRAME_BYTES = 4096
MAX_BUNDLE_CHARS = 65_536
PUSH_LIMIT_SECONDS = 15 * 60
MAX_BACKOFF_SECONDS = 300.0
METHOD = "config_sync"


class _Superseded(Exception):
    """A newer snapshot exists; the running push stops and the next tick restarts."""


class ConfigSyncService:
    def __init__(
        self,
        *,
        key: SigningKey,
        transport,
        online: Callable[[], dict[str, str]],
        recipients: Callable[[], list[str]],
        primary: Callable[[], str],
        device_name: Callable[[], str],
        peer_name: Callable[[str], str],
        after_apply: Callable[[Applied], Awaitable[None]] | None = None,
        notice: Callable[[str], None] | None = None,
        builder: SnapshotBuilder | None = None,
        root: Path | None = None,
        poll_seconds: float = POLL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._key = key
        self._transport = transport
        self._online = online
        self._recipients = recipients
        self._primary = primary
        self._device_name = device_name
        self._peer_name = peer_name
        self._after_apply = after_apply
        self._notice = notice
        self._root = root
        self._builder = builder or SnapshotBuilder(root=root)
        self._poll = poll_seconds
        self._sleep = sleep
        self._receiver = Receiver(key, root)
        self._receive_lock = asyncio.Lock()
        self._snapshot: Snapshot | None = None
        self._revision = 0
        self._floor = 0
        self._stamped: str | None = None
        # peer key -> (relay session, digest) the device last acknowledged
        self._delivered: dict[str, tuple[str, str]] = {}
        self._files_delivered: dict[str, tuple[str, str]] = {}
        self._failures: dict[str, int] = {}
        self._retry_at: dict[str, float] = {}
        self._pushes: dict[str, asyncio.Task] = {}
        self._task: asyncio.Task | None = None
        self._closed = False
        self._told_skipped: tuple[str, ...] = ()
        self._told_refused = False

    # ---- lifecycle ----

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="kollab-config-sync")

    async def close(self) -> None:
        self._closed = True
        tasks = [self._task, *self._pushes.values()]
        for task in tasks:
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(t for t in tasks if t is not None), return_exceptions=True
        )
        self._task = None
        self._pushes.clear()

    async def _loop(self) -> None:
        while not self._closed:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("config sync: tick failed (%s)", type(error).__name__)
            await self._sleep(self._poll)

    async def drain(self) -> None:
        """Wait for the pushes a tick started (tests and shutdown)."""
        pushes = [task for task in self._pushes.values() if not task.done()]
        if pushes:
            await asyncio.gather(*pushes, return_exceptions=True)

    # ---- primary ----

    async def tick(self) -> None:
        peers = self._online()
        targets = [(key, peers[key]) for key in self._recipients() if key in peers]
        if not targets:
            return
        try:
            snapshot = await asyncio.to_thread(self._builder.build)
        except ConfigSyncError:
            # The settings file is mid-write or damaged. Sending nothing is the
            # safe answer: an empty snapshot would erase what devices manage.
            logger.warning("config sync: settings are unreadable; nothing sent")
            return
        self._stamp(snapshot)
        for key, session in targets:
            if self._delivered.get(key) == (session, snapshot.digest):
                continue
            running = self._pushes.get(key)
            if (
                running is not None and not running.done()
            ) or time.monotonic() < self._retry_at.get(key, 0.0):
                continue
            self._pushes[key] = asyncio.create_task(
                self._push(key, session, snapshot, self._revision),
                name="kollab-config-push",
            )

    def _stamp(self, snapshot: Snapshot) -> None:
        if snapshot.digest != self._stamped:
            # Milliseconds since the epoch keep growing across restarts; a
            # device that says the number is stale raises the floor.
            self._revision = max(
                self._revision + 1, self._floor, int(time.time() * 1000)
            )
            self._stamped = snapshot.digest
        self._snapshot = snapshot

    async def _push(
        self, key: str, session: str, snapshot: Snapshot, revision: int
    ) -> None:
        try:
            async with asyncio.timeout(PUSH_LIMIT_SECONDS):
                blob = core_blob(snapshot, self._device_name())
                body = {
                    "digest": snapshot.digest,
                    "files_digest": snapshot.files_digest,
                }
                self._expect(
                    await self._call(key, "core", body, blob, revision, 30),
                    {"ok": True},
                )
                if self._files_delivered.get(key) != (session, snapshot.files_digest):
                    await self._push_files(key, snapshot, revision)
                    self._files_delivered[key] = (session, snapshot.files_digest)
            self._delivered[key] = (session, snapshot.digest)
            self._failures.pop(key, None)
            logger.info(
                "config sync: %d files and the settings are current on a device",
                len(snapshot.files),
            )
        except _Superseded:
            return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failures = self._failures[key] = self._failures.get(key, 0) + 1
            delay = min(MAX_BACKOFF_SECONDS, 10.0 * 2 ** (failures - 1))
            self._retry_at[key] = time.monotonic() + delay
            logger.warning(
                "config sync: a device did not take the settings (%s), retry in %ds",
                getattr(error, "code", type(error).__name__),
                delay,
            )

    async def _push_files(self, key: str, snapshot: Snapshot, revision: int) -> None:
        entries = list(snapshot.files)
        manifest = [[e.path, e.sha256, e.size, int(e.executable)] for e in entries]
        body = sync_body(snapshot)
        for _round in range(3):
            reply = await self._call(
                key, "sync", body, pack_json({"manifest": manifest}), revision, 60
            )
            self._expect(reply)
            if reply.get("applied") is True:
                return
            need = decode_need(reply.get("need"), len(entries))
            for batch in _batches(
                [entry for entry, wanted in zip(entries, need) if wanted]
            ):
                if self._snapshot.digest != snapshot.digest:
                    raise _Superseded
                items = [(entry, self._read(entry)) for entry in batch]
                reply = await self._call(key, "put", *pack_files(items), revision, 60)
                self._expect(reply)
        raise ConfigSyncError("incomplete")

    def _read(self, entry: FileEntry) -> bytes:
        try:
            data = (kollab_root(self._root) / entry.path).read_bytes()
        except OSError:
            raise _Superseded from None
        if hashlib.sha256(data).hexdigest() != entry.sha256:
            raise _Superseded  # edited since the snapshot; the next one has it
        return data

    async def _call(
        self, key: str, op: str, body: dict, blob: bytes, revision: int, timeout: float
    ) -> dict:
        sealed = seal(
            op,
            body,
            blob,
            issuer_key=self._key,
            recipient_public_key=bytes.fromhex(key),
            revision=revision,
        )
        payload = {"v": 1, "op": op, "bundle": base64.b64encode(sealed).decode("ascii")}
        started = time.monotonic()
        reply = await self._transport.request(key, METHOD, payload, timeout=timeout)
        frames = math.ceil(len(payload["bundle"]) / FRAME_BYTES) + 2
        await self._sleep(
            max(0.0, frames / FRAMES_PER_SECOND - (time.monotonic() - started))
        )
        return reply

    def _expect(self, reply: object, exactly: dict | None = None) -> None:
        if not isinstance(reply, dict):
            raise ConfigSyncError("invalid")
        error = reply.get("error")
        if error is not None:
            if error == "stale" and type(reply.get("revision")) is int:
                self._floor = max(self._floor, reply["revision"] + 1)
                self._stamped = None  # restamp above the device's revision
            raise ConfigSyncError(str(error)[:32])
        if exactly is not None and reply != exactly:
            raise ConfigSyncError("invalid")

    # ---- secondary ----

    async def receive(self, peer: str, payload: dict) -> dict:
        """One call from the device that issued this device's join code."""
        primary = self._primary()
        if not primary or peer != primary:
            return {"error": "not_primary"}
        if (
            not isinstance(payload, dict)
            or set(payload) != {"v", "op", "bundle"}
            or payload["v"] != 1
            or payload["op"] not in ("core", "sync", "put")
            or not isinstance(payload["bundle"], str)
            or len(payload["bundle"]) > MAX_BUNDLE_CHARS
        ):
            return {"error": "invalid"}
        try:
            sealed = base64.b64decode(payload["bundle"], validate=True)
        except ValueError:
            return {"error": "invalid"}
        applied: Applied | None = None
        async with self._receive_lock:
            try:
                if payload["op"] == "core":
                    reply, applied = await asyncio.to_thread(
                        self._receiver.core,
                        sealed,
                        primary_key=peer,
                        primary_name=self._peer_name(peer),
                    )
                elif payload["op"] == "sync":
                    reply = await asyncio.to_thread(
                        self._receiver.sync, sealed, primary_key=peer
                    )
                else:
                    reply = await asyncio.to_thread(
                        self._receiver.put, sealed, primary_key=peer
                    )
            except Exception as error:
                logger.warning(
                    "config sync: could not apply a bundle (%s)", type(error).__name__
                )
                return {"error": "failed"}
        self._tell(reply, applied)
        if (
            applied is not None
            and self._after_apply is not None
            and (applied.config_changed or applied.mcp_changed)
        ):
            try:
                await self._after_apply(applied)
            except Exception as error:
                logger.warning(
                    "config sync: refresh after apply failed (%s)", type(error).__name__
                )
        return reply

    def _tell(self, reply: dict, applied: Applied | None) -> None:
        """Tell the human once per cause, never once per bundle."""
        if self._notice is None:
            return
        if reply.get("error") == "other_primary" and not self._told_refused:
            # Another workspace on this machine joined a different network last and
            # took the machine's one managed-config record; every bundle is refused.
            self._told_refused = True
            self._notice(
                "Settings sync is off in this workspace: another workspace on this "
                "machine joined a different network last."
            )
        if applied is None or applied.skipped_mcp == self._told_skipped:
            return
        self._told_skipped = applied.skipped_mcp
        if applied.skipped_mcp:
            names = ", ".join(applied.skipped_mcp)
            self._notice(f"Skipped MCP servers not installed here: {names}")


def _batches(entries: list[FileEntry]) -> list[list[FileEntry]]:
    """Greedy groups that fit one secure request: compressed bytes and file count."""
    batches: list[list[FileEntry]] = []
    current: list[FileEntry] = []
    used = 0
    for entry in entries:
        if current and (
            used + entry.zsize > MAX_ZFILE_BYTES or len(current) >= MAX_BATCH_FILES
        ):
            batches.append(current)
            current, used = [], 0
        current.append(entry)
        used += entry.zsize
    if current:
        batches.append(current)
    return batches
