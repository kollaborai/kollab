"""Lightweight device client and host-side ambient speech bridge."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time
import uuid
from collections import deque
from datetime import datetime

from kollabor_agent.voice_admission import issue_device_voice_context, revoke_voice_admission_context
from kollabor_agent.voice_tool_policy import VOICE_TOOL_POLICY_ID
from kollabor_events import EventType, Hook, HookPriority
from kollabor_plugins.base import BasePlugin

logger = logging.getLogger(__name__)

PROGRESS_AFTER_S = (8, 17)  # still-working lines while the agent has said nothing
# A stopped delivery tries again on its own, waiting longer each time (seconds).
OBSERVER_RETRY_S, OBSERVER_RETRY_MAX_S = 5, 120
# /voice enroll: about 30 seconds read aloud, covering most English sounds.
ENROLL_PASSAGE = (
    "On quiet mornings I make coffee, open the window and listen to the birds. "
    "Then I check my notes, fix whatever broke overnight and plan the day. My "
    "favorite lunch is a warm bowl of soup with fresh bread. In the evening we "
    "walk the dog past the park, talk about the weather, and watch the sky turn "
    "orange, purple and gray before it gets dark."
)


class VoicePlugin(BasePlugin):

    # First voice.bind after activation rides the hub socket while the daemon
    # may still be busy with session startup; give it more than the 2s
    # steady-state budget before the retry ladder kicks in.
    BIND_FIRST_TIMEOUT_S = 5.0

    def __init__(
        self, name="voice", event_bus=None, renderer=None, config=None, **kwargs
    ):
        self.name = name
        self._event_bus, self._config = event_bus, config
        self.client_id = f"{os.getpid()}-{uuid.uuid4().hex}"
        self.requested = False
        self.visible = False
        self._state = {"state": "off"}
        self._client = None
        self._lease = None
        self._claim_id = None
        self._controller = None
        self._observer = None
        self._pending = []
        self._recent_transcripts = []
        self._last_transcript_arrival = 0
        self._observer_error = None
        self._speech_error = None
        self._retry = asyncio.Event()
        self._admissions = None
        self._admission_lock = asyncio.Lock()
        self._remote_binding = None
        self._remote_jobs = {}
        self._remote_heartbeat_task = None
        self._routing_error = None
        self._bound_epoch = None
        from kollabor_voice.speech_settings import load_speech_preferences

        self._preferences_revision = self._preference_revision()
        preferences = load_speech_preferences()
        self._voice_preferences = preferences
        self.context_lines = preferences["context_lines"]
        self._speech_api = None
        self._speech_api_profile = None
        self._speech_api_lock = asyncio.Lock()
        self._output_generation = 0
        self._seen_speech = {}
        self._last_speech_decision = None
        self._speech_review_state = None
        self._remote_reply_queue = asyncio.Queue(maxsize=32)
        self._remote_reply_task = None
        self._agent_names = ["Kollab"]
        self._progress_task = None
        self._spoken_replies = deque(maxlen=64)
        self._observer_backoff = OBSERVER_RETRY_S
        self._restart_delay = OBSERVER_RETRY_S  # before the client starts again

    @staticmethod
    def get_default_config():
        return {"kollabor": {"voice": {"enabled": False}}}

    async def initialize(self, args=None, **kwargs):
        self._event_bus = kwargs.get("event_bus", self._event_bus)
        self._config = kwargs.get("config", self._config)
        self._event_bus.register_service("voice_plugin", self)
        self.register_rpc()
        self._register_tool()
        # Deliberately never activate from a persisted or inherited enabled flag.

    async def register_hooks(self):
        await self._event_bus.register_hook(
            Hook(
                name="voice_spoken_reply",
                plugin_name="voice",
                event_type="voice_response_ready",
                callback=self._spoken_reply,
                priority=HookPriority.POSTPROCESSING.value,
            )
        )
        await self._event_bus.register_hook(
            Hook(
                name="voice_cancel_output",
                plugin_name="voice",
                event_type=EventType.CANCEL_REQUEST,
                callback=self._cancel_output,
                priority=HookPriority.POSTPROCESSING.value,
            )
        )

    def _refresh(self):
        loop = (
            self._event_bus.get_service("main_render_loop") if self._event_bus else None
        )
        if loop is not None:
            loop.request_render()

    def status(self):
        self.sync_preferences()
        state = dict(self._state)
        state.pop("classifier", None)  # an older voice service still reports one
        if self.requested and state.get("state") not in {"listening", "error"}:
            # Installing, loading models, waiting to claim the microphone: to
            # the person it is all voice starting, never "off".
            state["state"] = "starting"
        if self._speech_error:
            state["output"] = {**state.get("output", {}), "error": self._speech_error}
        state.update(
            requested=self.requested,
            visible=self.visible,
            running=self.requested and state.get("state") == "listening",
            observer_error=self._observer_error,
            pending=len(self._pending),
            context_lines=self.context_lines,
            last_speech_decision=self._last_speech_decision,
            speech_review_state=self._speech_review_state,
            pending_replies=self._remote_reply_queue.qsize(),
            activity=self._activity(state),
        )
        return state

    def _activity(self, state):
        """What voice is doing this moment, for the terminal and the web UI."""
        import math

        output = state.get("output") or {}
        if state.get("state") != "listening":
            stage = str(state.get("state") or "off")
        elif output.get("state") in {"synthesizing", "playing"}:
            stage = "speaking"
        elif state.get("speech_active"):
            stage = "hearing"
        elif state.get("transcribing") or state.get("input_depth"):
            stage = "transcribing"
        else:
            stage = "listening"
        speaker = state.get("speaker") or {}
        enrollment = speaker.get("enrollment")
        if enrollment and stage != "speaking" and state.get("state") == "listening":
            stage = f"enrolling {enrollment['seconds']:.0f}/{enrollment['target']:.0f}s"
        elif time.time() - (speaker.get("saved_at") or 0) < 10:
            stage = "voiceprint saved"
        level = float(state.get("level") or 0)
        # -60 dBFS (a quiet room) .. -15 dBFS (speech up close) as 0..1.
        loudness = (20 * math.log10(max(level, 1e-6)) + 60) / 45
        return {
            "stage": stage,
            "level": round(min(1.0, max(0.0, loudness)), 2),
        }

    @staticmethod
    def _preference_revision():
        from kollabor_voice.control import service_root

        try:
            stat = (service_root() / "preferences.json").stat()
            return stat.st_mtime_ns, stat.st_ino
        except OSError:
            return None

    def sync_preferences(self):
        """Other chats and interfaces save to the same device preference file."""
        revision = self._preference_revision()
        if revision == self._preferences_revision:
            return
        from kollabor_voice.speech_settings import load_speech_preferences

        preferences = load_speech_preferences()
        changed = preferences != self._voice_preferences
        self._voice_preferences = preferences
        self.context_lines = preferences["context_lines"]
        self._preferences_revision = revision
        if changed:
            self._observer_error = self._routing_error
            self._retry.set()
            self._refresh()

    async def configure_voice(self, changes):
        from kollabor_voice.speech_settings import save_speech_preferences

        save_speech_preferences(changes)
        self.sync_preferences()

    async def enroll_voice(self):
        """Save the user's voiceprint from their next 30 seconds of speech."""
        if not (self.requested and self._lease and self._client):
            raise RuntimeError(
                "Turn voice on first (/voice on), then run /voice enroll"
            )
        await self._client.call("speaker_enroll", **self._auth())
        self._refresh()
        return (
            "Voice enrollment: talk for 30 seconds, alone in a quiet room. Read this "
            "aloud, or say anything:\n\n" + ENROLL_PASSAGE + "\n\nNothing you say "
            "reaches the agent until it finishes; the status row counts the seconds. "
            "Only the voiceprint is kept, never the audio. Turn on Speaker Gate in "
            "/voice config to use it."
        )

    async def forget_voiceprint(self):
        """Delete the voiceprint, with or without the voice service running."""
        from kollabor_voice.client import VoiceClient
        from kollabor_voice.control import service_root

        try:
            result = await (self._client or VoiceClient()).call("speaker_forget")
            forgotten = result["forgotten"]
        except OSError, TimeoutError, ValueError, RuntimeError:
            # Not running, or a service from before the speaker gate.
            path = service_root() / "voiceprint.json"
            forgotten = path.exists()
            path.unlink(missing_ok=True)
        self._refresh()
        return "Voiceprint deleted" if forgotten else "No voiceprint was saved"

    def set_context_lines(self, value):
        from kollabor_voice.speech_settings import save_speech_preferences

        save_speech_preferences({"context_lines": value})
        self.sync_preferences()
        return (
            f"Voice context: previous {value} transcript lines. Saved for this device."
        )

    def _transcript_context_for(self, records, additional=()):
        """Prior microphone lines go to the agent as context, never as new requests."""
        ids = {r["event_id"] for r in records}
        epoch = (self._lease or {}).get("epoch")
        stream = (self._lease or {}).get("stream_id")
        first_seq = min((r["seq"] for r in records if "seq" in r), default=None)
        prior = [
            r
            for r in [*self._recent_transcripts, *additional]
            if r["event_id"] not in ids
            and (not epoch or r.get("owner_epoch", epoch) == epoch)
            and (not stream or r.get("stream_id", stream) == stream)
            and (first_seq is None or r.get("seq", -1) < first_seq)
        ]
        return list({r["event_id"]: r for r in prior}.values())[-self.context_lines :]

    def _remember_transcripts(self, records):
        from kollabor_voice.speech_settings import MAX_CONTEXT_LINES

        prior = {r["event_id"]: r for r in self._recent_transcripts}
        prior.update({r["event_id"]: r for r in records})
        self._recent_transcripts = list(prior.values())[-MAX_CONTEXT_LINES:]

    async def start_voice(self, *_legacy_identity):
        if self.requested:
            return "Voice is already active in this chat"
        self.requested = self.visible = True
        self._state = {"state": "starting", "detail": "Checking local voice service"}
        self._observer_error = None
        self._speech_error = None
        self._pending = []
        self._recent_transcripts = []
        self._controller = asyncio.create_task(self._run(), name="voice-client")
        self._refresh()
        return "Voice Starting — checking local service; setup runs in the background"

    async def _start_from_context(self):
        return await self.start_voice()

    async def stop_voice(self):
        self._output_generation += 1
        self._seen_speech.clear()
        self.requested = False
        if self._progress_task:
            self._progress_task.cancel()
        await self._stop_remote_replies()
        for task in (self._controller, self._observer, self._remote_heartbeat_task):
            if task and task is not asyncio.current_task():
                task.cancel()
        await asyncio.gather(
            *(
                t
                for t in (self._controller, self._observer, self._remote_heartbeat_task)
                if t and t is not asyncio.current_task()
            ),
            return_exceptions=True,
        )
        if self._claim_id and self._client:
            with contextlib.suppress(Exception):
                await self._client.call(
                    "withdraw", client_id=self.client_id, claim_id=self._claim_id
                )
        elif self._lease and self._client:
            with contextlib.suppress(Exception):
                await self._client.call("release", **self._auth())
        rpc = self._event_bus.get_service("rpc_client") if self._event_bus else None
        if rpc and self._lease:
            with contextlib.suppress(Exception):
                await rpc.call(
                    "voice.unbind", {"epoch": self._lease["epoch"]}, timeout=1
                )
        self._lease = None
        self._claim_id = None
        self._state = {"state": "off"}
        self._pending = []
        self._recent_transcripts = []
        self._observer_error = None
        self._speech_error = None
        self._routing_error = None
        self._bound_epoch = None
        self._refresh()
        return "Voice Off"

    async def retry_voice(self):
        if (
            self.requested
            and self._lease
            and self._observer_error
            and not self._routing_error
        ):
            self._observer_error = None
            self._retry.set()
            return "Voice retrying pending speech"
        await self.stop_voice()
        return await self.start_voice()

    def _auth(self):
        if not self._lease:
            raise RuntimeError("Voice is off")
        return {"token": self._lease["token"], "epoch": self._lease["epoch"]}

    async def _patient_call(self, op, **params):
        """A voice service call that rides out a busy service.

        Each call gets 2 s, but the service answers late while models load or a
        worker starts, and one late answer used to stop voice. A timeout is
        retried; a refusal (lease expired, not owner) still raises at once.
        """
        for attempt in range(4):
            try:
                return await self._client.call(op, **params)
            except TimeoutError as exc:
                if attempt == 3 or not self.requested:
                    raise
                logger.warning("%s; retrying (%d/3)", exc, attempt + 1)

    async def _run(self):
        from kollabor_voice.client import VoiceClient
        from kollabor_voice.control import VoiceError

        self._client = VoiceClient()
        activated = time.time()
        try:

            def update(state):
                self._state = state
                self._refresh()

            initial = await self._client.ensure(update)
            if (
                initial.get("state") == "error"
                or initial.get("speech", {}).get("state") == "error"
            ):
                initial = await self._patient_call("retry")
            # Model initialization can hold the Python runtime long enough to
            # exceed a capture lease. Acquire ownership only once loading ends.
            # Cancelling this task during setup therefore cannot open the mic.
            while not initial.get("ready"):
                update(initial)
                speech = initial.get("speech", {})
                if speech.get("state") == "error":
                    raise RuntimeError(
                        speech.get("detail", "Speech engine setup failed")
                    )
                if initial.get("state") == "error":
                    raise RuntimeError(
                        initial.get("error")
                        or initial.get("detail")
                        or "Voice setup failed"
                    )
                await asyncio.sleep(0.3)
                initial = await self._patient_call("status")
            if not self.requested:
                return
            self._claim_id = uuid.uuid4().hex
            # The service may be finishing a prior capture (teardown, VAD
            # load); its claim can exceed the client's 2s exchange budget
            # while still succeeding server-side. Retry instead of dying.
            for attempt in range(3):
                try:
                    self._lease = await self._client.call(
                        "claim",
                        client_id=self.client_id,
                        activated_at=activated,
                        claim_id=self._claim_id,
                    )
                    break
                except asyncio.TimeoutError:
                    if attempt == 2 or not self.requested:
                        raise
                    logger.warning(
                        "voice claim timed out (attempt %d/3); retrying", attempt + 1
                    )
                    await asyncio.sleep(0.5 * (attempt + 1))
            cursor = self._lease["cursor"]
            rpc = self._event_bus.get_service("rpc_client")
            if rpc:
                await self._bind_remote(rpc)
                if self._bound_epoch == self._lease["epoch"]:
                    self._remote_heartbeat_task = asyncio.create_task(
                        self._keep_remote_alive(rpc), name="voice-routing-heartbeat"
                    )
            else:
                self._agent_names = self._local_agent_names()
            # Whisper spells the names it is told about ("Aquamarine", not
            # "Awkwamari").
            with contextlib.suppress(Exception):
                await self._client.call("hint", **self._auth(), names=self._agent_names)
            self._observer = asyncio.create_task(
                self._deliver_loop(), name="voice-delivery"
            )
            healthy_since = time.monotonic()
            while self.requested:
                self.sync_preferences()
                self._state = await self._patient_call("heartbeat", **self._auth())
                if self._state.get("state") == "error":
                    # Capture stopped (the microphone or a worker failed):
                    # start again below rather than sit deaf.
                    raise RuntimeError(self._state.get("error") or "Voice failed")
                if time.monotonic() - healthy_since > 60:
                    self._restart_delay = OBSERVER_RETRY_S
                if len(self._pending) < 32:
                    result = await self._patient_call(
                        "read",
                        **self._auth(),
                        after=cursor,
                        stream_id=self._lease["stream_id"],
                    )
                    cursor = result["cursor"]
                    for record in result["records"]:
                        if record.get("final") and record.get("text"):
                            self._pending.append(record)
                            self._last_transcript_arrival = time.monotonic()
                    self._save_consumer(cursor)
                # Otherwise delivery is 32 lines behind: read on once it
                # catches up. The transcript is durable, so nothing is lost.
                self._refresh()
                await asyncio.sleep(0.35)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Voice client stopped: %r", exc)
            if self._observer:
                self._observer.cancel()
            if isinstance(exc, VoiceError) and exc.code in {"not_owner", "superseded"}:
                # Another chat took the microphone: voice stays off here.
                self._state = {"state": "active_elsewhere", "error": str(exc)}
                self.requested = False
                self._refresh()
                return
            # Anything else (the service died, the lease expired, setup failed)
            # starts again on its own, waiting longer each time. /voice retry
            # skips the wait and /voice off cancels it.
            delay = self._restart_delay
            self._restart_delay = min(delay * 2, OBSERVER_RETRY_MAX_S)
            self._state = {
                "state": "starting",
                "detail": f"Voice stopped ({exc}); restarting in {delay:g}s",
            }
            self._refresh()
            await asyncio.sleep(delay)
            if self.requested:
                await self.stop_voice()
                await self.start_voice()

    async def _bind_remote(self, rpc):
        """Bind the lease to remote routing, surviving daemon cold-start congestion.

        The first bind rides the hub socket seconds after session start, when the
        daemon's event loop is still chewing startup work; a 2s timeout can fire
        with the path perfectly healthy (it replies in milliseconds once warm).
        Retry with backoff, and never treat a bind failure as fatal: local
        microphone and durable transcription work without remote routing, same
        policy as _keep_remote_alive.
        """
        from kollabor_rpc import RpcTimeoutError

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                bound = await rpc.call(
                    "voice.bind",
                    {"epoch": self._lease["epoch"], "client_id": self.client_id},
                    timeout=self.BIND_FIRST_TIMEOUT_S if attempt == 0 else 2,
                )
                self._agent_names = (bound or {}).get("names") or self._agent_names
                self._bound_epoch = self._lease["epoch"]
                self._routing_error = None
                return
            except RpcTimeoutError as exc:
                last_exc = exc
                logger.warning(
                    "voice.bind timed out (attempt %d/3): %s", attempt + 1, exc
                )
                await asyncio.sleep(0.5 * (attempt + 1))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Method missing / handler error means routing is genuinely
                # unavailable; stop retrying, stay non-fatal.
                logger.warning("voice.bind unavailable, continuing local-only: %s", exc)
                self._routing_error = str(exc)
                return
        self._routing_error = str(last_exc)
        logger.warning(
            "voice.bind timed out after retries; voice stays local-only: %s",
            last_exc,
        )

    async def _keep_remote_alive(self, rpc):
        """Keep remote routing bound, and bind again on its own when it is lost.

        A slow heartbeat is a busy daemon, not a dead one (dreaming or a long
        tool can hold its event loop for seconds), so a timeout never ends
        routing. A refused one (the binding expired or was replaced) binds
        again with backoff. Meanwhile the microphone and durable transcription
        keep working and pending speech waits, visible, until routing is back.
        """
        from kollabor_rpc import RpcTimeoutError

        failures = 0
        while self.requested:
            try:
                await rpc.call(
                    "voice.heartbeat", {"epoch": self._lease["epoch"]}, timeout=2.5
                )
            except asyncio.CancelledError:
                raise
            except RpcTimeoutError as exc:
                logger.warning("voice.heartbeat timed out; still routing: %s", exc)
                continue
            except Exception as exc:
                failures += 1
                self._routing_error = str(exc)
                self._observer_error = f"AI connection lost; reconnecting ({exc})"
                self._refresh()
                await asyncio.sleep(min(30, 2**failures))
                await self._bind_remote(rpc)
                if self._routing_error is None:
                    logger.info("Voice routing restored after %d attempt(s)", failures)
                    failures = 0
                    self._observer_error = None
                    self._retry.set()
                    self._refresh()
                continue
            await asyncio.sleep(0.35)

    def _save_consumer(self, cursor):
        from kollabor_voice.control import atomic_json

        path = self._client.root / "consumers"
        path.mkdir(exist_ok=True, mode=0o700)
        atomic_json(
            path / f"{self.client_id}.json",
            {
                "epoch": self._lease["epoch"],
                "stream_id": self._lease["stream_id"],
                "cursor": cursor,
                "pending": self._pending,
                "recent_transcripts": self._recent_transcripts,
                "updated_at": time.time(),
            },
        )

    async def _deliver_loop(self):
        """Send each final transcript straight to the agent turn: no intent gate."""
        from kollabor_voice.observer import is_playback_echo, strip_playback

        while self.requested:
            if not self._pending:
                await asyncio.sleep(0.15)
                continue
            # The microphone also hears this agent's own voice. Those lines stay
            # in the voice transcript but are never delivered.
            echoes = [r for r in self._pending if is_playback_echo(r)]
            if echoes:
                dropped = {r["event_id"] for r in echoes}
                self._pending[:] = [
                    r for r in self._pending if r["event_id"] not in dropped
                ]
                self._remember_transcripts(echoes)
                logger.info(
                    "Dropped %d microphone line(s) echoing playback", len(echoes)
                )
                continue
            # The speaker gate heard another voice (the TV, other people), or
            # the user reading for enrollment: never delivered. Other voices
            # stay as context for later turns.
            gated = [
                r
                for r in self._pending
                if r.get("speaker") in {"other_speaker", "enrolling"}
            ]
            if gated:
                dropped = {r["event_id"] for r in gated}
                self._pending[:] = [
                    r for r in self._pending if r["event_id"] not in dropped
                ]
                self._remember_transcripts(
                    [r for r in gated if r["speaker"] == "other_speaker"]
                )
                continue
            for record in self._pending:
                # Talked over voice: send only the human's words.
                own = strip_playback(record)
                if own and own != record["text"]:
                    record["heard_text"], record["text"] = record["text"], own
            if self._observer_error:
                # /voice retry retries at once; otherwise try again after a
                # pause, waiting longer each time. Routing errors heal in
                # _keep_remote_alive, which clears them when bound again.
                try:
                    await asyncio.wait_for(self._retry.wait(), self._observer_backoff)
                except TimeoutError:
                    if not self._routing_error:
                        logger.info("Voice delivery retrying: %s", self._observer_error)
                        self._observer_error = None
                        self._observer_backoff = min(
                            self._observer_backoff * 2, OBSERVER_RETRY_MAX_S
                        )
                        self._refresh()
                self._retry.clear()
                continue
            # Audio chunks are storage/Whisper boundaries, not conversational
            # turns. Wait for capture and transcription to settle, then send
            # the complete utterance.
            if (
                self._state.get("speech_active")
                or self._state.get("transcribing")
                or self._state.get("input_depth", 0)
                or time.monotonic() - self._last_transcript_arrival < 0.7
            ):
                await asyncio.sleep(0.15)
                continue
            records = self._pending[:8]
            epoch = self._lease["epoch"]
            try:
                newest = datetime.fromisoformat(records[-1]["ended_at"]).timestamp()
                if time.time() - newest > 30:
                    # Heard while voice could not deliver (an outage, a cold
                    # start): too late to answer now. It stays in the transcript
                    # as context, but is never sent.
                    del self._pending[: len(records)]
                    self._remember_transcripts(records)
                    continue
                # More of the utterance arrived while settling: send it whole.
                if self._state.get("speech_active") or len(self._pending) > len(
                    records
                ):
                    if len(records) < 8:
                        continue
                if not self.requested or epoch != self._lease["epoch"]:
                    return
                # Recheck actual device ownership before dispatch.
                await self._client.call("heartbeat", **self._auth())
                await self._admit_speech(records, epoch, newest)
                del self._pending[: len(records)]
                self._remember_transcripts(records)
                self._observer_backoff = OBSERVER_RETRY_S
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._observer_error = str(exc) or "Voice delivery timed out"
                logger.warning("Voice delivery pending: %s", self._observer_error)
            self._refresh()

    async def _admit_speech(self, selected, epoch, newest):
        from kollabor_voice.observer import admission_id, playback_text

        identity = admission_id(self.client_id, selected)
        data = {
            "message": " ".join(r["text"] for r in selected),
            "source": "voice",
            "voice": {
                "admission_id": identity,
                "reply_id": identity,
                "epoch": epoch,
                "client_id": self.client_id,
                "event_ids": [r["event_id"] for r in selected],
                "transcript_text": " ".join(r["text"] for r in selected),
                "transcript_context": self._transcript_context_for(selected),
                "playback_context": playback_text(selected),
                "expires_at": newest + 30,
            },
        }
        rpc = self._event_bus.get_service("rpc_client")
        result = (
            await self._remote_job(rpc, "voice.admit", data)
            if rpc
            else await self._deliver(data)
        )
        if result.get("status") != "queued":
            raise RuntimeError(
                f"Voice delivery {result.get('status', 'unknown')}; pending speech was not replayed"
            )

    async def _deliver(self, data):
        result = await self._event_bus.emit_with_hooks(
            EventType.USER_INPUT, data, "voice"
        )
        # Hook return values are stored in hook_results, not transformed into
        # final_data. The durable admission is the authoritative acknowledgment.
        if self._admissions:
            accepted = self._admissions.lookup(data["voice"]["admission_id"])
            if accepted:
                return accepted
        for phase in ("post", "main", "pre"):
            for hook in (result.get(phase) or {}).get("hook_results", []):
                if hook.get("hook_key") == "llm_core.process_user_input" and hook.get(
                    "success"
                ):
                    value = hook.get("result") or {}
                    if value.get("status"):
                        return value
        return {"status": "delivery_unknown"}

    async def validate_turn(self, voice):
        # Accepted work can take longer than the input admission window. Output
        # is fenced by current ownership, not the age of the original transcript.
        if self._remote_binding and voice.get("epoch") == self._remote_binding["epoch"]:
            if time.monotonic() - self._remote_binding["heartbeat"] < 3:
                return
        if (
            not self.requested
            or not self._lease
            or voice.get("epoch") != self._lease["epoch"]
        ):
            raise RuntimeError("Voice response belongs to an inactive conversation")
        await self._client.call("heartbeat", **self._auth())

    async def admit(self, data, submit):
        from kollabor_voice.control import service_root
        from kollabor_voice.observer import AdmissionStore

        voice = data["voice"]
        async with self._admission_lock:
            if voice.get("expires_at") and voice["expires_at"] < time.time():
                raise RuntimeError("Voice request expired before admission")
            await self.validate_turn(voice)
            if self._admissions is None:
                self._admissions = AdmissionStore(service_root() / "admissions.sqlite3")
            previous = self._admissions.reserve(voice["admission_id"], data["message"])
            if previous:
                return previous
            # The turn's tool grant: the voice policy governs it under every
            # approval mode, as a LiveKit room's turn. A lease is one voice
            # session with one generation.
            context = issue_device_voice_context(
                {
                    "voice_session_id": str(voice["epoch"]),
                    "generation": 1,
                    "turn_id": voice["admission_id"],
                    "voice_tool_policy": VOICE_TOOL_POLICY_ID,
                }
            )
            # If submit/commit is interrupted, delivery_unknown survives on disk.
            result = await submit(context)
            if result["status"] != "queued":
                revoke_voice_admission_context(context)
            self._admissions.finish(voice["admission_id"], result["status"])
            if result["status"] == "queued":
                self._watch_progress(voice)
            return {**result, "admission_id": voice["admission_id"]}

    def _watch_progress(self, voice):
        """Runs where the agent runs: only that side knows it is still working."""
        if self._progress_task:
            self._progress_task.cancel()
        self._progress_task = asyncio.create_task(
            self._progress(voice), name="voice-progress"
        )

    async def _progress(self, voice):
        """A still-working line while the agent works on a turn it has not answered."""
        from kollabor_voice.acks import pick

        llm = self._event_bus.get_service("llm_service")
        line = None
        for n, delay in enumerate(PROGRESS_AFTER_S):
            await asyncio.sleep(delay)
            if voice["reply_id"] in self._spoken_replies or not getattr(
                llm, "is_processing", False
            ):
                return
            line = pick("still_working", line)
            try:
                await self.voice_out(
                    line,
                    f"{voice['reply_id']}:progress:{n}",
                    voice["epoch"],
                    "voice-progress",
                )
            except Exception as exc:
                logger.warning("Voice progress line not spoken: %s", exc)
                return

    async def voice_out(
        self, text, reply_id, owner_epoch, producer_id, rewrite_count=0
    ):
        from kollabor_voice.speech_review import SpeechDecision, format_decision

        generation = self._output_generation
        voice = {"epoch": owner_epoch}
        await self.validate_turn(voice)
        if self._remote_binding and owner_epoch == self._remote_binding["epoch"]:
            from kollabor_tui.display_tap import publish_semantic

            publish_semantic(
                self._event_bus,
                "voice_reply",
                spoken_text=text,
                reply_id=reply_id,
                owner_epoch=owner_epoch,
                producer_id=producer_id,
                rewrite_count=rewrite_count,
            )
            return {"status": "forwarded_to_device"}
        self._last_speech_decision = (
            format_decision(text) or SpeechDecision("speak")
        ).to_wire()
        if generation != self._output_generation:
            return {"status": "cancelled", "ids": []}
        await self.validate_turn(voice)
        return await self._client.voice_out(
            text, reply_id, owner_epoch, producer_id, self._lease["token"]
        )

    async def _speech_request(self, instructions, data):
        """Tool-free agent-side rewrite; credentials never enter the device worker."""
        import copy

        from kollabor_ai.api_communication_service import APICommunicationService

        async with self._speech_api_lock:
            llm = self._event_bus.get_service("llm_service")
            api = getattr(llm, "api_service", None)
            if api is None:
                raise RuntimeError(
                    "The responding agent is unavailable to rewrite speech"
                )
            profile = copy.deepcopy(api._profile)
            key = hashlib.sha256(
                json.dumps(profile.to_dict(), sort_keys=True, default=str).encode()
            ).hexdigest()
            if self._speech_api is None or key != self._speech_api_profile:
                if self._speech_api:
                    await self._speech_api.shutdown()
                profile.max_tokens, profile.effort = 256, "low"
                self._speech_api = APICommunicationService(self._config, None, profile)
                self._speech_api.enable_streaming = False
                if not await self._speech_api.initialize():
                    raise RuntimeError("Cannot initialize the speech rewrite provider")
                self._speech_api_profile = key
            return await asyncio.wait_for(
                self._speech_api.call_llm(
                    [
                        {"role": "system", "content": instructions},
                        {
                            "role": "user",
                            "content": json.dumps(data, ensure_ascii=False),
                        },
                    ],
                    tools=[],
                ),
                10,
            )

    async def _rewrite_speech(self, text, reason):
        from kollabor_voice.speech_review import SPEECH_NUDGE

        rpc = self._event_bus.get_service("rpc_client")
        if rpc:
            result = await self._remote_job(
                rpc,
                "voice.rewrite_speech",
                {
                    "epoch": self._lease["epoch"],
                    "text": text,
                    "reason": reason,
                },
            )
            return result["spoken_text"]
        if len(text) > 16000:
            raise RuntimeError(
                "Answer is too long to rewrite automatically; provide spoken_text explicitly"
            )
        return await self._speech_request(
            SPEECH_NUDGE, {"reason": reason, "answer": text}
        )

    async def _spoken_reply(self, data, event=None):
        from kollabor_voice.output import silent_response

        voice = data.get("voice")
        if not voice:
            return data
        # The agent answered this turn: no more still-working lines for it.
        self._spoken_replies.append(voice.get("reply_id"))
        spoken = data.get("spoken_text")
        display = data.get("display_text", "")
        if (spoken is not None and (not spoken.strip() or silent_response(spoken))) or (
            spoken is None and silent_response(display)
        ):
            return data
        identity = (
            voice["epoch"],
            voice["reply_id"],
            hashlib.sha256(json.dumps([display, spoken]).encode()).hexdigest(),
        )
        if identity in self._seen_speech:
            return data
        if len(self._seen_speech) >= 256:
            self._seen_speech.pop(next(iter(self._seen_speech)))
        self._seen_speech[identity] = True
        generation = self._output_generation
        try:
            await self.validate_turn(voice)
            self._speech_error = None
            rewritten = bool(data.get("rewrite_count", 0))
            if spoken is None:
                # Screen text stays on the responding host. It is never a speech
                # candidate: ask that agent for the missing narration first.
                if not display:
                    return data
                self._speech_review_state = "shortening reply"
                self._refresh()
                spoken = await self._rewrite_speech(
                    display, "Missing spoken_text; display_text is screen-only"
                )
                rewritten = True
            while True:
                if generation != self._output_generation:
                    return data
                await self.validate_turn(voice)
                result = await self.voice_out(
                    spoken,
                    voice["reply_id"],
                    voice["epoch"],
                    data.get("producer_id", self.client_id),
                    rewrite_count=int(rewritten),
                )
                if result.get("status") != "rewrite":
                    self._speech_error = None
                    break
                reason = result.get("review", {}).get(
                    "reason", "Reply is unsuitable for speech"
                )
                if rewritten:
                    # Never dropped after its one rewrite: said with code,
                    # links and paths taken out (Marco, 2026-10-18).
                    from kollabor_voice.speech_review import speakable

                    spoken = speakable(spoken)
                    if not spoken:
                        raise RuntimeError(
                            f"Nothing speakable left after one rewrite: {reason}"
                        )
                    await self.voice_out(
                        spoken,
                        voice["reply_id"],
                        voice["epoch"],
                        data.get("producer_id", self.client_id),
                        rewrite_count=1,
                    )
                    self._speech_error = None
                    break
                self._speech_review_state = "shortening reply"
                self._refresh()
                spoken = await self._rewrite_speech(spoken, reason)
                rewritten = True
        except Exception as exc:
            self._speech_error = f"Spoken reply skipped: {exc}"
            logger.warning("%s", self._speech_error)
        finally:
            self._speech_review_state = None
            self._refresh()
        return data

    async def handle_remote_reply(self, data):
        # Called on the attach socket's sole reader. Review/rewrite can call RPC
        # on that same socket, so enqueue and return before awaiting any speech.
        if (
            self.requested
            and self._lease
            and data.get("owner_epoch") == self._lease["epoch"]
        ):
            try:
                self._remote_reply_queue.put_nowait(
                    (
                        self._output_generation,
                        {
                            "spoken_text": data["spoken_text"],
                            "voice": {
                                "reply_id": data["reply_id"],
                                "epoch": data["owner_epoch"],
                            },
                            "producer_id": data.get("producer_id", "remote"),
                            "rewrite_count": data.get("rewrite_count", 0),
                        },
                    )
                )
            except asyncio.QueueFull:
                self._speech_error = "Spoken reply skipped: remote reply queue is full"
                logger.warning(self._speech_error)
                self._refresh()
                return
            if self._remote_reply_task is None or self._remote_reply_task.done():
                self._remote_reply_task = asyncio.create_task(
                    self._drain_remote_replies(), name="voice-remote-replies"
                )
            self._refresh()

    async def _drain_remote_replies(self):
        # One worker preserves update/final order while the socket reader remains
        # available to route the RPC responses needed by this worker.
        while not self._remote_reply_queue.empty():
            generation, data = self._remote_reply_queue.get_nowait()
            try:
                if (
                    generation == self._output_generation
                    and self.requested
                    and self._lease
                    and data["voice"]["epoch"] == self._lease["epoch"]
                ):
                    await self._spoken_reply(data)
            finally:
                self._remote_reply_queue.task_done()
                self._refresh()

    async def _stop_remote_replies(self):
        task, self._remote_reply_task = self._remote_reply_task, None
        while not self._remote_reply_queue.empty():
            self._remote_reply_queue.get_nowait()
            self._remote_reply_queue.task_done()
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _cancel_output(self, data, event=None):
        self._output_generation += 1
        if self._progress_task:
            self._progress_task.cancel()
        await self._stop_remote_replies()
        if self._lease and self._client:
            with contextlib.suppress(Exception):
                await self._client.call("cancel", **self._auth())
        return data

    def _register_tool(self):
        executor = self._event_bus.get_service("tool_executor")
        if executor:
            executor.register_plugin_handler("voice_out", self._voice_tool)

    async def _voice_tool(self, tool_data):
        from kollabor_agent.tool_executor import ToolExecutionResult

        llm = self._event_bus.get_service("llm_service")
        voice = getattr(getattr(llm, "_queue_processor", None), "active_voice", None)
        args = tool_data.get("params", tool_data)
        try:
            if not voice:
                raise RuntimeError(
                    "voice_out requires an active voice turn or an explicitly delegated output capability"
                )
            result = await self.voice_out(
                args["text"], voice["reply_id"], voice["epoch"], self.client_id
            )
            if result.get("status") == "rewrite":
                from kollabor_voice.speech_review import SPEECH_NUDGE

                raise RuntimeError(result["review"]["reason"] + ". " + SPEECH_NUDGE)
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "voice_out"),
                tool_type="voice_out",
                success=True,
                output=json.dumps(result),
            )
        except Exception as exc:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "voice_out"),
                tool_type="voice_out",
                success=False,
                error=str(exc),
            )

    def register_rpc(self):
        server = self._event_bus.get_service("rpc_server") if self._event_bus else None
        if server is None:
            return
        methods = {
            "voice.bind": self._remote_bind,
            "voice.unbind": self._remote_unbind,
            "voice.heartbeat": self._remote_heartbeat,
            "voice.admit": self._remote_admit,
            "voice.result": self._remote_result,
            "voice.rewrite_speech": self._remote_rewrite_speech,
        }
        for name, handler in methods.items():
            if name not in server.list_methods():
                server.register(name, handler)

    async def _remote_bind(self, params):
        for task in self._remote_jobs.values():
            task.cancel()
        self._remote_jobs.clear()
        self._remote_binding = {**params, "heartbeat": time.monotonic()}
        return {"bound": True, "names": self._local_agent_names()}

    def _local_agent_names(self):
        """What the human calls this agent: Kollab, and its hub name when it has one."""
        hub = self._event_bus.get_service("hub_plugin") if self._event_bus else None
        name = getattr(getattr(hub, "_identity", None), "identity", None)
        return ["Kollab", name] if name else ["Kollab"]

    def _check_remote(self, params):
        if (
            not self._remote_binding
            or params.get("epoch") != self._remote_binding["epoch"]
            or time.monotonic() - self._remote_binding["heartbeat"] >= 3
        ):
            raise RuntimeError("Remote voice lease expired")

    async def _remote_heartbeat(self, params):
        # Arriving at all proves the window is alive. One this daemon read late
        # (its own event loop was busy: dreaming, a long tool) renews the
        # binding; refusing it ended voice routing until /voice retry.
        binding = self._remote_binding
        if not binding or params.get("epoch") != binding["epoch"]:
            raise RuntimeError("Remote voice lease expired")
        binding["heartbeat"] = time.monotonic()
        return {"alive": True}

    async def _remote_unbind(self, params):
        if (
            self._remote_binding
            and params.get("epoch") == self._remote_binding["epoch"]
        ):
            self._remote_binding = None
            for task in self._remote_jobs.values():
                task.cancel()
        return {"released": True}

    async def _remote_rewrite_speech(self, params):
        self._check_remote(params)

        async def rewrite():
            text = await self._rewrite_speech(params["text"], params["reason"])
            self._check_remote(params)
            return {"spoken_text": text}

        return self._start_remote_job(rewrite())

    async def _remote_admit(self, params):
        self._check_remote(params["voice"])
        return self._start_remote_job(self._deliver(params))

    def _start_remote_job(self, work):
        # The attach transport dispatches RPCs serially. Never hold its reader
        # while doing inference or waiting for host startup/admission.
        for key, task in list(self._remote_jobs.items()):
            if len(self._remote_jobs) >= 32 and task.done():
                del self._remote_jobs[key]
        if len(self._remote_jobs) >= 32:
            work.close()
            raise RuntimeError("Remote voice request queue is full")
        identity = uuid.uuid4().hex
        task = asyncio.create_task(work, name="remote-voice-request")
        task.add_done_callback(
            lambda task: task.exception() if not task.cancelled() else None
        )
        self._remote_jobs[identity] = task
        return {"job_id": identity}

    async def _remote_result(self, params):
        self._check_remote(params)
        task = self._remote_jobs.get(params["job_id"])
        if task is None:
            raise RuntimeError("Remote voice result expired")
        if not task.done():
            return {"done": False}
        if task.cancelled():
            raise RuntimeError("Remote voice request was cancelled")
        return {"done": True, "result": task.result()}

    async def _remote_job(self, rpc, method, params):
        async with asyncio.timeout(12):
            ticket = await rpc.call(method, params, timeout=2)
            while True:
                result = await rpc.call(
                    "voice.result",
                    {
                        "job_id": ticket["job_id"],
                        "epoch": self._lease["epoch"],
                    },
                    timeout=2,
                )
                if result["done"]:
                    return result["result"]
                await asyncio.sleep(0.15)

    async def shutdown(self):
        await self.stop_voice()
        for task in self._remote_jobs.values():
            task.cancel()
        await asyncio.gather(*self._remote_jobs.values(), return_exceptions=True)
        if self._speech_api:
            await self._speech_api.shutdown()
        if self._admissions:
            self._admissions.close()
