"""Lightweight device client and host-side ambient speech bridge."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import itertools
import json
import logging
import os
import time
import uuid
from collections import deque
from datetime import datetime

from kollabor_events import EventType, Hook, HookPriority
from kollabor_plugins.base import BasePlugin

logger = logging.getLogger(__name__)

# Speech routed without a classifier call, as /voice classifier shows it.
VOICE_ROUTES = {
    "addressed": "Called by name, or the words right after a greeting",
    "confirmed": 'Answered yes to "was that for me?"',
    "declined": "Not for the agent (the answer to its question); quiet for two minutes",
}

# Turn-taking around the classifier's own lines (seconds).
CLARIFY_WINDOW_S = 12  # "was that for me?" waits this long after voice stops talking
CLARIFY_PAUSE_S = 30  # nobody answered it: likely the TV, stop asking a while
QUIET_S = 120  # "no, it's the TV": no clarifying questions for two minutes
FOLLOW_UP_S = 15  # after "hey aquamarine" alone, the next words are for the agent
CONTEXT_MESSAGES = 6  # recent chat messages the classifier reads (three exchanges)
PROGRESS_AFTER_S = (8, 17)  # still-working lines while the agent has said nothing
# A stopped observer tries again on its own, waiting longer each time (seconds).
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
        self._api = None
        self._api_profile = None
        self._pending = []
        self._recent_ignored = []
        self._recent_transcripts = []
        self._decision_transcript_context = []
        self._last_transcript_arrival = 0
        self._observer_error = None
        self._speech_error = None
        self._observing = False
        self._retry = asyncio.Event()
        self._admissions = None
        self._admission_lock = asyncio.Lock()
        self._remote_binding = None
        self._remote_jobs = {}
        self._remote_heartbeat_task = None
        self._routing_error = None
        self._bound_epoch = None
        from kollabor_voice.classifiers import load_preferences

        self._preferences_revision = self._preference_revision()
        preferences = load_preferences()
        self._voice_preferences = preferences
        self.classifier_name = preferences["classifier"]
        self.context_lines = preferences["context_lines"]
        self._needs_classifier_prepare = False
        self._classifier_generation = 0
        self._last_decision = None
        self._decision_deferred_ids = []
        # /voice classifier: each classifier call, oldest first.
        self._classifier_log = deque(maxlen=50)
        self._classifier_seq = itertools.count(1)
        self._classifier_entry = None
        self._speech_api = None
        self._speech_api_profile = None
        self._speech_api_lock = asyncio.Lock()
        self._output_generation = 0
        self._seen_speech = {}
        self._last_speech_decision = None
        self._speech_review_state = None
        self._remote_reply_queue = asyncio.Queue(maxsize=32)
        self._remote_reply_task = None
        # The classifier's own lines (kollabor_voice.acks) and the turn-taking
        # around them.
        self._agent_names = ["Kollab"]
        self._clarifying = None  # {"records", "asked"}: awaiting "was that for me?"
        self._quiet_until = self._clarify_pause_until = self._follow_up_until = 0.0
        self._ignored_at = deque(maxlen=8)  # when speech was judged not for us
        self._said = {}  # kind -> last line, so no line repeats back to back
        self._last_said = None  # {"text", "at"}: the latest line, for the UI
        self._progress_task = None
        self._spoken_replies = deque(maxlen=64)
        self._spoke_at = 0.0  # when voice last synthesized or played speech
        self._acked = {}  # event ids -> the ack already said for them
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
            observing=self._observing,
            classifier_name=self.classifier_name,
            context_lines=self.context_lines,
            last_decision=self._last_decision,
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
        elif self._observing:
            stage = "deciding"
        elif self._clarifying and time.monotonic() <= self._question_ends():
            stage = "asking"
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
        said = self._last_said
        return {
            "stage": stage,
            "level": round(min(1.0, max(0.0, loudness)), 2),
            "said": said["text"] if said and time.time() - said["at"] < 10 else None,
        }

    def _question_ends(self):
        """When "was that for me?" stops waiting: a while after voice last spoke.

        Counted from the end of everything voice said after asking (the agent's
        reply can play in between), so the human always gets the full window.
        """
        held = self._clarifying
        return max(held["asked"], self._spoke_at) + CLARIFY_WINDOW_S if held else 0.0

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
        from kollabor_voice.classifiers import load_preferences

        preferences = load_preferences()
        changed = preferences != self._voice_preferences
        self._voice_preferences = preferences
        if self.classifier_name != preferences["classifier"]:
            self._needs_classifier_prepare = preferences["classifier"] == "laya"
        self.classifier_name = preferences["classifier"]
        self.context_lines = preferences["context_lines"]
        self._preferences_revision = revision
        if changed:
            self._classifier_generation += 1
            self._last_decision = None
            self._observer_error = self._routing_error
            self._retry.set()
            self._refresh()

    async def configure_voice(self, changes):
        from kollabor_voice.classifiers import save_preferences

        save_preferences(changes)
        self.sync_preferences()
        if "classifier" in changes:
            # Selecting the current classifier also retries a stopped worker.
            self._classifier_generation += 1
            self._last_decision = None
            self._observer_error = self._routing_error
            self._needs_classifier_prepare = self.classifier_name == "laya"
            self._retry.set()
        if self._needs_classifier_prepare and self._client and self.requested:
            await self._prepare_classifier()
            self._needs_classifier_prepare = False

    async def set_classifier(self, name):
        from kollabor_voice.classifiers import CLASSIFIERS

        if name not in CLASSIFIERS:
            raise ValueError("Choose laya or provider")
        await self.configure_voice({"classifier": name})
        self._retry.set()
        self._refresh()
        return f"Voice classifier: {CLASSIFIERS[name]}. Saved for this device; pending speech is preserved."

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
        from kollabor_voice.classifiers import save_context_lines

        save_context_lines(value)
        self.sync_preferences()
        return (
            f"Voice context: previous {value} transcript lines. Saved for this device."
        )

    def _transcript_context_for(self, records, additional=()):
        """Prior microphone lines inform a decision but are never new candidates."""
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
        from kollabor_voice.classifiers import MAX_CONTEXT_LINES

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
        self._recent_ignored = []
        self._recent_transcripts = []
        self._decision_transcript_context = []
        self._controller = asyncio.create_task(self._run(), name="voice-client")
        self._refresh()
        return "Voice Starting — checking local service; setup runs in the background"

    async def _start_from_context(self):
        return await self.start_voice()

    async def stop_voice(self):
        self._output_generation += 1
        self._seen_speech.clear()
        self.requested = False
        self._clarifying, self._follow_up_until = None, 0.0
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
        self._recent_ignored = []
        self._recent_transcripts = []
        self._decision_transcript_context = []
        self._observer_error = None
        self._speech_error = None
        self._routing_error = None
        self._bound_epoch = None
        self._refresh()
        return "Voice Off"

    async def _prepare_classifier(self):
        # Cache the state the service just entered. Until the next heartbeat the
        # observer would read the old "error" and latch it again.
        prepared = await self._patient_call("classifier_prepare")
        self._state = {**self._state, "classifier": prepared}

    async def retry_voice(self):
        if self.classifier_name == "laya" and self._client and self.requested:
            await self._prepare_classifier()
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
            if self.classifier_name == "laya":
                await self._patient_call("classifier_prepare")
                initial = await self._patient_call("status")
            # Model initialization can hold the Python runtime long enough to
            # exceed a capture lease. Acquire ownership only once loading ends.
            # Cancelling this task during setup therefore cannot open the mic.
            while not initial.get("ready") or (
                self.classifier_name == "laya"
                and initial.get("classifier", {}).get("state") != "ready"
            ):
                classifier = initial.get("classifier", {})
                if initial.get("ready") and self.classifier_name == "laya":
                    update({**initial, **classifier})
                    if classifier.get("state") == "error":
                        raise RuntimeError(
                            classifier.get("detail", "Laya setup failed")
                        )
                else:
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
            if self.classifier_name == "laya":
                await self._patient_call("classifier_prepare")
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
            # "Awkwamari"), and the classifier hears them as a direct address.
            with contextlib.suppress(Exception):
                await self._client.call("hint", **self._auth(), names=self._agent_names)
            self._observer = asyncio.create_task(
                self._observe_loop(), name="voice-observer"
            )
            healthy_since = time.monotonic()
            while self.requested:
                self.sync_preferences()
                if self._needs_classifier_prepare:
                    await self._prepare_classifier()
                    self._needs_classifier_prepare = False
                self._state = await self._patient_call("heartbeat", **self._auth())
                if self._state.get("state") == "error":
                    # Capture stopped (the microphone or a worker failed):
                    # start again below rather than sit deaf.
                    raise RuntimeError(self._state.get("error") or "Voice failed")
                if time.monotonic() - healthy_since > 60:
                    self._restart_delay = OBSERVER_RETRY_S
                output = self._state.get("output") or {}
                if output.get("state") in {"synthesizing", "playing"}:
                    self._spoke_at = time.monotonic()
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
                # Otherwise the observer is 32 lines behind: read on once it
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

    async def _observe_loop(self):
        from kollabor_voice.observer import is_playback_echo, strip_playback

        while self.requested:
            if not self._pending:
                await asyncio.sleep(0.15)
                continue
            # The microphone also hears this agent's own voice. Those lines stay
            # in the voice transcript but are never classified or delivered.
            echoes = [r for r in self._pending if is_playback_echo(r)]
            if echoes:
                dropped = {r["event_id"] for r in echoes}
                self._pending[:] = [
                    r for r in self._pending if r["event_id"] not in dropped
                ]
                self._log_classifier(echoes, echo=True)
                logger.info(
                    "Dropped %d microphone line(s) echoing playback", len(echoes)
                )
                continue
            # The speaker gate heard another voice (the TV, other people), or
            # the user reading for enrollment: never classified or delivered.
            # Other voices stay as context for later decisions.
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
                for kind in ("other_speaker", "enrolling"):
                    lines = [r for r in gated if r["speaker"] == kind]
                    if lines:
                        self._log_classifier(lines, **{kind: True})
                self._remember_transcripts(
                    [r for r in gated if r["speaker"] == "other_speaker"]
                )
                continue
            for record in self._pending:
                # Talked over voice: judge and send only the human's words.
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
                        logger.info("Voice observer retrying: %s", self._observer_error)
                        self._observer_error = None
                        self._needs_classifier_prepare = (
                            self._state.get("classifier", {}).get("state") == "error"
                        )
                        self._observer_backoff = min(
                            self._observer_backoff * 2, OBSERVER_RETRY_MAX_S
                        )
                        self._refresh()
                self._retry.clear()
                continue
            if self.classifier_name == "laya":
                classifier = self._state.get("classifier", {})
                if classifier.get("state") == "error":
                    self._observer_error = classifier.get(
                        "detail", "Laya failed; retrying"
                    )
                    self._refresh()
                    continue
                if classifier.get("state") != "ready":
                    await asyncio.sleep(0.15)
                    continue
            # Audio chunks are storage/Whisper boundaries, not conversational
            # turns. Wait for capture and transcription to settle before asking
            # whether the complete utterance addresses this chat.
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
            classifier_generation = self._classifier_generation
            self._classifier_entry = None
            try:
                self._observing = True
                newest = datetime.fromisoformat(records[-1]["ended_at"]).timestamp()
                if time.time() - newest > 30:
                    # Heard while voice could not decide (an outage, a cold
                    # start): too late to answer now. It stays in the transcript
                    # and informs what comes next, but is never sent.
                    self._log_classifier(
                        records,
                        decision={
                            "decision": "ignore",
                            "detail": "Older than 30 seconds by the time voice "
                            "could decide: kept as context, not sent",
                        },
                    )
                    del self._pending[: len(records)]
                    self._remember_transcripts(records)
                    continue
                # An initially ignored lead-in can be followed by a question.
                # Keep a bounded lookback so that early classification cannot
                # erase the start of the user's next contiguous request.
                from kollabor_voice.observer import connected_speech

                earliest = datetime.fromisoformat(records[0]["started_at"]).timestamp()
                previous = [
                    r
                    for r in self._recent_ignored
                    if earliest - datetime.fromisoformat(r["ended_at"]).timestamp() <= 2
                ]
                candidates = previous + records
                from kollabor_voice.acks import address, answer

                heard = " ".join(r["text"] for r in records)
                held = self._clarifying
                if held and time.monotonic() > self._question_ends():
                    # Nobody answered "was that for me?": likely background.
                    self._clarify_pause_until = self._question_ends() + CLARIFY_PAUSE_S
                    held = self._clarifying = None
                reply = answer(heard) if held else None
                self._decision_deferred_ids = []
                if reply == "no":
                    selected, how = [], "declined"
                elif reply == "yes":
                    # A bare yes sends the held speech; a longer answer goes too.
                    extra = records if len(heard.split()) > 3 else []
                    selected, how = held["records"] + extra, "confirmed"
                elif (
                    address(heard, self._agent_names)[0]
                    or time.monotonic() < self._follow_up_until
                ):
                    selected, how = connected_speech(candidates, records), "addressed"
                else:
                    selected = await asyncio.wait_for(
                        self.observe_records(candidates), 10
                    )
                    if classifier_generation != self._classifier_generation:
                        continue
                    selected = connected_speech(candidates, selected)
                    how = "unsure" if self._decision_deferred_ids else "classified"
                # If more of the utterance arrived during inference, decide on
                # the larger batch before admitting any fragment to the host.
                if self._state.get("speech_active") or len(self._pending) > len(
                    records
                ):
                    if len(records) < 8:
                        if self._classifier_entry:
                            self._classifier_entry["note"] = (
                                "More speech arrived; decided again with it"
                            )
                        continue
                if not self.requested or epoch != self._lease["epoch"]:
                    return
                # Recheck actual device ownership after inference, before dispatch.
                await self._client.call("heartbeat", **self._auth())
                # Speech that is neither an answer nor for the agent (the TV
                # talking on) leaves the question open until its window ends.
                asking = held is not None and not reply and how == "unsure"
                if reply or (selected and how != "unsure"):
                    self._clarifying = None
                if how in VOICE_ROUTES:
                    self._classifier_entry = self._log_classifier(
                        records,
                        decision={
                            "decision": "ignore" if how == "declined" else "respond",
                            "detail": VOICE_ROUTES[how],
                        },
                    )
                if how == "declined":
                    self._quiet_until = time.monotonic() + QUIET_S
                elif selected and not asking:
                    await self._respond(selected, candidates, how, epoch, newest)
                else:
                    self._ignored_at.append(time.time())
                del self._pending[: len(records)]
                self._remember_transcripts(records)
                chosen = {r["event_id"] for r in selected}
                self._recent_ignored = [
                    r for r in candidates if r["event_id"] not in chosen
                ][-8:]
                self._observer_backoff = OBSERVER_RETRY_S
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if classifier_generation != self._classifier_generation:
                    continue
                self._observer_error = str(exc) or "Voice observer timed out"
                if self._classifier_entry is None:
                    self._log_classifier(records, error=self._observer_error)
                elif not self._classifier_entry.get("error"):
                    self._classifier_entry["error"] = self._observer_error
                logger.warning("Voice observation pending: %s", self._observer_error)
            finally:
                self._observing = False
            self._refresh()

    async def _respond(self, selected, candidates, how, epoch, newest):
        """Answer at once in the classifier's own words; the agent gets what needs it.

        how: "classified" (sure), "unsure", "addressed" (called by name) or
        "confirmed" (yes to the classifier's question).
        """
        from kollabor_voice.acks import kind_of

        text = " ".join(r["text"] for r in selected)
        kind = "confirmed" if how == "confirmed" else kind_of(text, self._agent_names)
        if how == "unsure" and kind != "mic_check":
            now = time.monotonic()
            if kind == "thanks" or now < max(
                self._quiet_until, self._clarify_pause_until
            ):
                if self._classifier_entry is not None:
                    self._classifier_entry["note"] = "Unsure, and not asking right now"
                return
            self._clarifying = {"records": selected, "asked": now}
            await self._say(
                "clarify_background" if self._background_heard() else "clarify", epoch
            )
            return
        key = tuple(r["event_id"] for r in selected)
        if key not in self._acked:
            # Said once even when admission fails and the observer retries.
            self._acked[key] = await self._say(kind, epoch)
            while len(self._acked) > 32:
                self._acked.pop(next(iter(self._acked)))
        # After a bare name, the next words are for the agent too.
        self._follow_up_until = (
            time.monotonic() + FOLLOW_UP_S if kind == "greeting" else 0.0
        )
        # A mic check, thanks or bare name was answered just now and still goes
        # to the agent with that answer: it may add more or hold off.
        await self._admit_speech(
            selected,
            candidates,
            epoch,
            newest,
            self._acked[key],
            answered=kind in {"mic_check", "thanks", "greeting"},
        )

    def _background_heard(self):
        """Speech kept arriving that was not for us: probably a TV in the room."""
        return sum(t > time.time() - 90 for t in self._ignored_at) >= 2

    async def _say(self, kind, epoch):
        """Speak one of the classifier's lines now; returns it, or "" if voice could not."""
        from kollabor_voice.acks import pick

        line = pick(kind, self._said.get(kind))
        self._said[kind] = line
        try:
            # Curated lines skip the spoken-style review agent replies get.
            await self._client.call(
                "voice_out",
                text=line,
                reply_id=f"line-{uuid.uuid4().hex}",
                epoch=epoch,
                producer_id="voice-classifier",
                token=self._lease["token"],
            )
        except Exception as exc:
            logger.warning("Voice line %r not spoken: %s", line, exc)
            return ""
        self._last_said = {"text": line, "at": time.time()}
        if self._classifier_entry is not None:
            self._classifier_entry["said"] = line
        self._refresh()
        return line

    async def _admit_speech(
        self, selected, candidates, epoch, newest, ack, answered=False
    ):
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
                # An earlier ignored utterance in this same batch informed
                # classification too. Keep it for the host without replaying
                # it as a fresh request.
                "transcript_context": self._transcript_context_for(
                    selected, candidates
                ),
                "playback_context": playback_text(selected),
                "classifier": self._last_decision,
                # Already said aloud; the agent continues from it.
                "spoken_ack": ack,
                # Voice answered it (a mic check, thanks, a bare name): nobody
                # is waiting, so no still-working lines.
                "answered": answered,
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
        if self._classifier_entry:
            self._classifier_entry["delivered"] = [r["event_id"] for r in selected]

    async def observe_records(self, records):
        from kollabor_voice.classifiers import (
            DecisionRequest,
            LayaClassifier,
            ProviderClassifier,
        )

        if not records:
            return []
        if self.classifier_name == "laya":
            classifier = LayaClassifier(self._client, self._auth)
            rpc = self._event_bus.get_service("rpc_client")
            context = (
                (
                    await rpc.call(
                        "voice.context", {"epoch": self._lease["epoch"]}, timeout=2
                    )
                )["context"]
                if rpc
                else self._conversation_context()
            )
        else:
            classifier = ProviderClassifier(self._observe_with_provider)
            context = ""
        self._decision_transcript_context = self._transcript_context_for(records)
        entry = self._classifier_entry = self._log_classifier(
            records,
            context=context,
            transcript_context=self._decision_transcript_context,
        )
        started = time.monotonic()
        try:
            decision = await classifier.decide(
                DecisionRequest(
                    records,
                    context,
                    self._decision_transcript_context,
                    self.context_lines,
                )
            )
            self._last_decision = entry["decision"] = decision.to_wire()
            self._decision_deferred_ids = decision.deferred_event_ids
            return decision.selected(records)
        except Exception as exc:
            entry["error"] = str(exc) or type(exc).__name__
            raise
        finally:
            entry["seconds"] = round(time.monotonic() - started, 3)

    def classifier_log(self):
        """Each classifier call: the request, the answer, what reached the agent."""
        return list(self._classifier_log)

    def _log_classifier(self, records, **fields):
        entry = {
            "seq": next(self._classifier_seq),
            "at": time.time(),
            "classifier": self.classifier_name,
            "records": records,
            "decision": None,
            "error": None,
            "delivered": [],
            **fields,
        }
        self._classifier_log.append(entry)
        return entry

    def _conversation_context(self):
        from kollabor_ai.message_content import content_to_text

        llm = self._event_bus.get_service("llm_service")
        history = getattr(llm, "conversation_history", [])
        recent = []
        for message in reversed(history):
            if message.role not in {"user", "assistant"}:
                continue
            metadata = getattr(message, "metadata", None) or {}
            voice = metadata.get("voice") or {}
            content = voice.get("transcript_text", message.content)
            if isinstance(content, list):
                content = [
                    part
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
            if not isinstance(content, (str, list)):
                continue
            text = content_to_text(content).strip()
            if voice and "transcript_text" not in voice:
                import re

                text = re.sub(
                    r"<agent_hud>.*?</agent_hud>", "", text, flags=re.S
                ).strip()
                # Older saved voice turns embedded their routing instructions
                # in content. They are not part of the human's spoken context.
                before, marker, after = text.partition("[Voice instructions: ")
                if marker:
                    text = (before + after.partition("]\n")[2]).strip()
            if text:
                recent.append(f"{message.role}: {text[:200]}")
                if len(recent) == CONTEXT_MESSAGES:
                    break
        return "\n".join(reversed(recent))

    async def _observe_with_provider(self, records, transcript_context=None):
        from kollabor_voice.observer import (
            OBSERVE_INSTRUCTIONS,
            parse_decision,
        )

        if not records:
            return []
        from kollabor_voice.classifiers import MAX_CONTEXT_LINES

        transcript_context = transcript_context or []
        if len(transcript_context) > MAX_CONTEXT_LINES:
            raise ValueError("Transcript context exceeds 50 lines")

        rpc = self._event_bus.get_service("rpc_client")
        if rpc:
            result = await self._remote_job(
                rpc,
                "voice.observe",
                {
                    "epoch": self._lease["epoch"],
                    "records": records,
                    "transcript_context": transcript_context,
                },
            )
            if result.get("context_event_ids", []) != [
                r["event_id"] for r in transcript_context
            ]:
                raise RuntimeError(
                    "Attached agent did not acknowledge transcript context; restart that agent"
                )
            return [r for r in records if r["event_id"] in result["event_ids"]]
        llm = self._event_bus.get_service("llm_service")
        if llm is None:
            raise RuntimeError("AI provider is not ready; speech remains pending")
        api = getattr(llm, "api_service", None)
        if api is None:
            raise RuntimeError("AI provider is unavailable")
        profile = api._profile
        profile_key = hashlib.sha256(
            json.dumps(profile.to_dict(), sort_keys=True, default=str).encode()
        ).hexdigest()
        if self._api is None or self._api_profile != profile_key:
            import copy

            from kollabor_ai.api_communication_service import APICommunicationService

            if self._api:
                await self._api.shutdown()
            observer_profile = copy.deepcopy(profile)
            observer_profile.max_tokens = 512
            observer_profile.effort = "low"
            self._api = APICommunicationService(self._config, None, observer_profile)
            self._api.enable_streaming = False
            if not await self._api.initialize():
                raise RuntimeError(
                    "Voice observer cannot initialize the configured AI provider"
                )
            self._api_profile = profile_key
        # 2000 characters is a conservative bound below 2000 text tokens.
        context = self._conversation_context()
        messages = [
            {"role": "system", "content": OBSERVE_INSTRUCTIONS},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "recent_conversation": context,
                        "recent_transcripts": transcript_context,
                        "transcripts": records,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        response = await self._api.call_llm(messages, tools=[])
        return parse_decision(response, records)

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
            # If submit/commit is interrupted, delivery_unknown survives on disk.
            result = await submit()
            self._admissions.finish(voice["admission_id"], result["status"])
            if (
                result["status"] == "queued"
                and voice.get("spoken_ack")
                and not voice.get("answered")
            ):
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
        self, text, reply_id, owner_epoch, producer_id, rewrite_count=0, review=None
    ):
        generation = (self._output_generation, self._classifier_generation)
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
        if review is None:
            review = await self._review_speech(text, rewritten=bool(rewrite_count))
        self._last_speech_decision = review.to_wire()
        if generation != (self._output_generation, self._classifier_generation):
            return {"status": "cancelled", "ids": []}
        await self.validate_turn(voice)
        return await self._client.voice_out(
            text,
            reply_id,
            owner_epoch,
            producer_id,
            self._lease["token"],
            review=review.to_wire(),
        )

    async def _speech_request(self, instructions, data):
        """Tool-free agent-side review/rewrite; credentials never enter the device worker."""
        import copy

        from kollabor_ai.api_communication_service import APICommunicationService

        async with self._speech_api_lock:
            llm = self._event_bus.get_service("llm_service")
            api = getattr(llm, "api_service", None)
            if api is None:
                raise RuntimeError(
                    "The responding agent is unavailable for speech review"
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
                    raise RuntimeError("Cannot initialize speech review provider")
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

    async def _review_speech(self, text, *, rewritten=False):
        from kollabor_voice.speech_review import SpeechDecision, format_decision

        fixed = format_decision(text)
        if fixed:
            return fixed
        if self.classifier_name == "laya":
            local = await self._client.review_speech(
                text, self._lease["epoch"], self._lease["token"]
            )
            adjudicate = rewritten and local.decision == "rewrite"
            if local.decision != "defer" and not adjudicate:
                return local
            self._speech_review_state = (
                "checking rewritten reply"
                if adjudicate
                else "checking uncertain spoken reply"
            )
            self._refresh()
            # One provider decision breaks repeated local style rejection, while
            # the format check above and the single rewrite budget stay intact.
            result = await self._provider_speech_review(text, local.reason)
            return SpeechDecision(
                result.decision,
                f"{'Laya rejected rewrite' if adjudicate else 'Laya unsure'}; {result.reason}",
                "laya+provider",
            )
        return await self._provider_speech_review(text)

    async def _provider_speech_review(self, text, classifier_note=""):
        from kollabor_voice.speech_review import SPEECH_REVIEW_PROMPT, SpeechDecision

        rpc = self._event_bus.get_service("rpc_client")
        if rpc:
            result = await self._remote_job(
                rpc,
                "voice.review_speech",
                {
                    "epoch": self._lease["epoch"],
                    "text": text,
                    "classifier_note": classifier_note,
                },
            )
            return SpeechDecision.from_wire(result)
        raw = await self._speech_request(
            SPEECH_REVIEW_PROMPT,
            {"spoken_text": text, "classifier_note": classifier_note},
        )
        value = json.loads(raw)
        if value.get("decision") not in {"speak", "rewrite"}:
            raise RuntimeError("Speech provider returned an invalid decision")
        return SpeechDecision(
            value["decision"], str(value.get("reason", "")), "provider"
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
        generation = (self._output_generation, self._classifier_generation)
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
                if generation != (self._output_generation, self._classifier_generation):
                    return data
                await self.validate_turn(voice)
                self._speech_review_state = "checking spoken reply"
                self._refresh()
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
                    from kollabor_voice.speech_review import SpeechDecision, speakable

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
                        review=SpeechDecision(
                            "speak", f"Spoken after one rewrite: {reason}"
                        ),
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
                        (self._output_generation, self._classifier_generation),
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
                    generation == (self._output_generation, self._classifier_generation)
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
            "voice.observe": self._remote_observe,
            "voice.admit": self._remote_admit,
            "voice.result": self._remote_result,
            "voice.context": self._remote_context,
            "voice.review_speech": self._remote_review_speech,
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

    async def _remote_observe(self, params):
        self._check_remote(params)
        return self._start_remote_job(self._do_remote_observe(params))

    async def _remote_review_speech(self, params):
        self._check_remote(params)

        async def review():
            # Explicit provider selection and uncertain Laya decisions use the
            # same tool-free reviewer on the host that owns the AI profile.
            from kollabor_voice.speech_review import format_decision

            fixed = format_decision(params["text"])
            if fixed:
                return fixed.to_wire()
            result = await self._provider_speech_review(
                params["text"], params.get("classifier_note", "")
            )
            self._check_remote(params)
            return result.to_wire()

        return self._start_remote_job(review())

    async def _remote_rewrite_speech(self, params):
        self._check_remote(params)

        async def rewrite():
            text = await self._rewrite_speech(params["text"], params["reason"])
            self._check_remote(params)
            return {"spoken_text": text}

        return self._start_remote_job(rewrite())

    async def _remote_context(self, params):
        self._check_remote(params)
        return {"context": self._conversation_context()}

    async def _do_remote_observe(self, params):
        history = params.get("transcript_context", [])
        selected = await asyncio.wait_for(
            self._observe_with_provider(params["records"], transcript_context=history),
            10,
        )
        self._check_remote(params)
        return {
            "event_ids": [r["event_id"] for r in selected],
            "context_event_ids": [r["event_id"] for r in history],
        }

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
        if self._api:
            await self._api.shutdown()
        if self._speech_api:
            await self._speech_api.shutdown()
        if self._admissions:
            self._admissions.close()
