"""Voice session control: events, routing glue and speech output for LiveKit rooms.

Wires EngineRuntimeHost and EngineSessionRouter into the live engine. A final
transcript enters the selected session through the router (ordinary admission
and PermissionManager) as a /turns call; the reply streams back to the worker
as one speech-ready sentence per line, and the session's own events become
voice events. A /turns client that disconnects before "done" is a barge-in.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from kollabor_agent.voice_admission import (
    add_voice_admission_revoke_listener,
    revoke_voice_admissions_for_turn,
)

from .voice_livekit import LiveKitRuntime
from .voice_runtime_host import EngineRuntimeHost, ResolvedVoiceTarget, RuntimeHostError
from .voice_session_router import EngineSessionRouter
from .voice_types import (
    CreateVoiceSessionRequest,
    JoinVoiceSessionRequest,
    StopVoiceSessionRequest,
    VoiceSessionBinding,
    VoiceSessionTicket,
    VoiceSessionView,
    VoiceTranscriptSnapshot,
    _FrozenModel,
)

logger = logging.getLogger(__name__)

OWNER_ID = "engine"
REPLAY_RING = 100
CHUNK_BYTES = 1500
MAX_TRANSCRIPT_BYTES = 16 * 1024
TURN_IDLE_TIMEOUT_S = 600.0
PARTIAL_INTERVAL_S = 0.25
MAX_SPOKEN_CHARS = 600
TERMINAL_STATES = {"stopped", "error", "resource_shortage"}
GREETING_INSTRUCTION = "Greet the user briefly and ask what they want to talk about."
_LOCAL_PROVIDERS = {"ollama", "lmstudio", "local", "mlx", "llamacpp"}


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


# --- speech output policy ---------------------------------------------------



def format_for_speech(text: str) -> str:
    """Deterministic speakable form of visible text; display_text is untouched.

    The voice service applies the same spoken_form before any engine speaks,
    so the transcript's spoken_text is exactly what is heard.
    """
    from kollabor_voice.spoken_form import spoken_form

    out = spoken_form(text)
    if len(out) > MAX_SPOKEN_CHARS:
        cut = out[:MAX_SPOKEN_CHARS]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        out = cut[: end + 1] if end > 0 else cut
    return out


_SPOKEN_OPEN = re.compile(r"<spoken_text\s*>", re.I)
_SPOKEN_CLOSE = re.compile(r"</spoken_text\s*>", re.I)
# A sentence ends at . ! ? (plus closing quotes) before whitespace, or at a blank line.
# ponytail: "Dr. Smith" cuts too; a pause there is harmless, a abbreviation list is not worth it.
_BOUNDARY = re.compile(r"(?<=[.!?])[\"')\]]*(?=\s)|\n\s*\n")


def spoken_so_far(raw: str) -> str | None:
    """The speakable text of a reply that is still streaming; None until it is known.

    split_response_channels(partial=True) reads an unclosed <spoken_text> as
    malformed and answers "", so a stream reads the channel itself: what follows
    the opening tag up to its closing tag, minus a closing tag still arriving.
    A reply with no channel tags is all speech; one with only <display_text>
    cannot say what is spoken until the reply ends.
    """
    match = _SPOKEN_OPEN.search(raw)
    if match:
        body = raw[match.end() :]
        close = _SPOKEN_CLOSE.search(body)
        if close:
            return body[: close.start()]
        cut = body.rfind("<")
        if cut >= 0 and "</spoken_text>".startswith(body[cut:].lower()):
            body = body[:cut]
        return body
    if "<display_text" in raw.lower():
        return None
    from kollabor_ai.response_channels import display_response_text

    return display_response_text(raw, partial=True)


def cut_sentences(buffer: str, *, final: bool) -> tuple[list[str], str]:
    """The complete sentences in `buffer` and the unfinished rest (flushed when final)."""
    sentences: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(buffer):
        piece = buffer[start : match.end()].strip()
        if piece:
            sentences.append(piece)
        start = match.end()
    rest = buffer[start:]
    if final and rest.strip():
        sentences.append(rest.strip())
        rest = ""
    return sentences, rest


class SpeechOutputDecision(_FrozenModel):
    speak: bool
    transcript: VoiceTranscriptSnapshot
    reason_code: str | None = None


class SpeechOutputPolicy:
    @staticmethod
    def sentence(text: str) -> str:
        """One sentence in speakable form; "" when nothing is left to say."""
        spoken = format_for_speech(text)
        # A lone period is the voice rule for a silent reply.
        return spoken if any(ch.isalnum() for ch in spoken) else ""

    def prepare(
        self, response: VoiceTranscriptSnapshot, binding: VoiceSessionBinding
    ) -> SpeechOutputDecision:
        if response.speaker != "assistant" or not response.final:
            return SpeechOutputDecision(
                speak=False, transcript=response, reason_code="invalid_request"
            )
        if response.voice_session_id != binding.voice_session_id:
            return SpeechOutputDecision(
                speak=False, transcript=response, reason_code="target_mismatch"
            )
        # The model's own spoken_text is the preferred source; the formatter
        # only runs on whatever text will be said.
        spoken = format_for_speech(
            response.spoken_text
            if response.spoken_text is not None
            else response.display_text
        )
        if not spoken:
            return SpeechOutputDecision(
                speak=False,
                transcript=response.model_copy(update={"spoken_text": None}),
            )
        return SpeechOutputDecision(
            speak=True, transcript=response.model_copy(update={"spoken_text": spoken})
        )


# --- disclosure ---------------------------------------------------------------


def llm_disclosure(profile: Any) -> tuple[bool, str | None]:
    """Whether the selected session's LLM sits behind a non-local URL."""
    provider = str(getattr(profile, "provider", "") or "").lower()
    url = next(
        (
            str(getattr(profile, n, "") or "")
            for n in ("base_url", "api_url", "endpoint", "url")
            if getattr(profile, n, "")
        ),
        "",
    )
    host = urlparse(url).hostname if url else None
    if host:
        if host == "localhost" or host.endswith(".localhost"):
            return False, host
        try:
            ip = ipaddress.ip_address(host)
            return not (ip.is_loopback or ip.is_private), host
        except ValueError:
            return True, host
    return provider not in _LOCAL_PROVIDERS, None


# --- session glue -------------------------------------------------------------


class RegistryTargetResolver:
    def __init__(self, registry: Callable[[], dict[str, Any]], settings: Any = None) -> None:
        self._registry = registry
        self._settings = settings

    async def resolve(
        self, owner_id: str, control_session_id: str, selected_thread_id: str | None
    ) -> ResolvedVoiceTarget:
        session = self._registry().get(control_session_id)
        if session is None:
            raise RuntimeHostError(
                "session_not_found", 404, "Voice session target was not found."
            )
        if not session.alive:
            raise RuntimeHostError(
                "session_busy",
                409,
                "The selected session is not running.",
                retryable=True,
            )
        if selected_thread_id is not None and selected_thread_id not in getattr(
            session, "known_thread_ids", ()
        ):
            raise RuntimeHostError(
                "target_mismatch",
                409,
                "The selected thread does not belong to this session.",
            )
        daemon = getattr(session, "daemon", None)
        agent_id = session.agent or getattr(daemon, "agent_name", "") or "default"
        profile = getattr(session, "profile", None)
        session_profile = str(getattr(profile, "name", "") or "default")
        settings = self._settings
        return ResolvedVoiceTarget(
            agent_id=agent_id,
            thread_id=selected_thread_id,
            # The voice profile and prompt overlay ride on voice turns only.
            voice_provider_profile_id=(
                settings.voice_profile_id(session_profile) if settings else session_profile
            ),
            voice_prompt_revision=(
                settings.prompt_revision(agent_id) if settings else "default"
            ),
            # ponytail: no lease epoch exists engine-side yet
            service_lease_epoch=0,
        )


class RegistrySessionLookup:
    def __init__(self, registry: Callable[[], dict[str, Any]]) -> None:
        self._registry = registry

    async def get_owned_session(self, owner_id: str, session_id: str) -> Any:
        return self._registry().get(session_id)


# --- events -------------------------------------------------------------------


class VoiceTranscriptChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript_id: str
    voice_session_id: str
    turn_id: str
    generation: int
    speaker: Literal["user", "assistant"]
    revision: int
    final: bool
    chunk_index: int
    chunk_count: int
    display_text_chunk: str
    spoken_text_chunk: str | None = None
    language: str | None = None
    observed_at_utc_ms: int


class WorkerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: Literal["voice.state", "voice.transcript", "voice.metrics", "voice.error"]
    turn_id: str | None = None
    generation: int | None = None
    payload: dict[str, Any] = {}


def _split_bytes(text: str, size: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for ch in text:
        if len((current + ch).encode()) > size:
            parts.append(current)
            current = ch
        else:
            current += ch
    parts.append(current)
    return parts


def transcript_chunks(snap: VoiceTranscriptSnapshot) -> list[dict[str, Any]]:
    display = _split_bytes(snap.display_text, CHUNK_BYTES)
    spoken = (
        _split_bytes(snap.spoken_text, CHUNK_BYTES)
        if snap.spoken_text is not None
        else []
    )
    count = max(len(display), len(spoken))
    return [
        VoiceTranscriptChunk(
            transcript_id=snap.transcript_id,
            voice_session_id=snap.voice_session_id,
            turn_id=snap.turn_id,
            generation=snap.generation,
            speaker=snap.speaker,
            revision=snap.revision,
            final=snap.final,
            chunk_index=i,
            chunk_count=count,
            display_text_chunk=display[i] if i < len(display) else "",
            spoken_text_chunk=(
                (spoken[i] if i < len(spoken) else "")
                if snap.spoken_text is not None
                else None
            ),
            language=snap.language,
            observed_at_utc_ms=snap.observed_at_utc_ms,
        ).model_dump(mode="json")
        for i in range(count)
    ]


def encode_cursor(voice_session_id: str, generation: int, sequence: int) -> str:
    raw = json.dumps(
        {"v": voice_session_id, "g": generation, "s": sequence}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(value: str) -> tuple[str, int, int]:
    try:
        data = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        return str(data["v"]), int(data["g"]), int(data["s"])
    except ValueError, KeyError, TypeError:
        raise RuntimeHostError(
            "invalid_request", 400, "The event cursor is malformed."
        ) from None


class _Stream:
    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.sequence = 0
        self.ring: deque[dict[str, Any]] = deque(maxlen=REPLAY_RING)


class VoiceEventHub:
    """Per-session replay ring (latest generation only) and live fan-out."""

    def __init__(self) -> None:
        self._streams: dict[str, _Stream] = {}
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any] | None]]] = {}

    def publish(
        self,
        view: VoiceSessionView,
        event: str,
        payload: dict[str, Any],
        *,
        turn_id: str | None = None,
    ) -> dict[str, Any] | None:
        stream = self._streams.get(view.voice_session_id)
        if stream is None or view.generation > stream.generation:
            stream = self._streams[view.voice_session_id] = _Stream(view.generation)
        elif view.generation < stream.generation:
            return None  # stale callback
        stream.sequence += 1
        envelope = {
            "schema_version": 1,
            "event_id": str(uuid4()),
            "event": event,
            "control_session_id": view.control_session_id,
            "conversation_session_id": view.conversation_session_id,
            "voice_session_id": view.voice_session_id,
            "turn_id": turn_id,
            "generation": view.generation,
            "sequence": stream.sequence,
            "occurred_at_utc_ms": _now_ms(),
            "payload": payload,
        }
        stream.ring.append(envelope)
        for queue in list(self._subs.get(view.voice_session_id, ())):
            if queue.qsize() >= REPLAY_RING:
                # Slow reader: drop it; it reconnects with its cursor.
                self._subs[view.voice_session_id].discard(queue)
                queue.put_nowait(None)
            else:
                queue.put_nowait(envelope)
        return envelope

    def close(self, voice_session_id: str) -> None:
        for queue in self._subs.pop(voice_session_id, set()):
            queue.put_nowait(None)

    def forget(self, voice_session_id: str) -> None:
        self._streams.pop(voice_session_id, None)

    def attach(
        self, view: VoiceSessionView, cursor: tuple[str, int, int] | None
    ) -> tuple[list[dict[str, Any]], asyncio.Queue[dict[str, Any] | None]]:
        """Backlog + live queue registered atomically (no await in between)."""
        stream = self._streams.get(view.voice_session_id)
        last = stream.sequence if stream and stream.generation == view.generation else 0
        backlog: list[dict[str, Any]] = []

        def reset(reason: str) -> dict[str, Any]:
            return {
                "schema_version": 1,
                "event_id": str(uuid4()),
                "event": "voice.cursor_reset",
                "control_session_id": view.control_session_id,
                "conversation_session_id": view.conversation_session_id,
                "voice_session_id": view.voice_session_id,
                "turn_id": None,
                "generation": view.generation,
                "sequence": last,
                "occurred_at_utc_ms": _now_ms(),
                "payload": {
                    "reason": reason,
                    "current_generation": view.generation,
                    "next_sequence": last + 1,
                    "snapshot": view.model_dump(mode="json"),
                },
            }

        if cursor is None:
            backlog.append(self._snapshot(view, last))
        elif cursor[1] != view.generation:
            backlog.append(reset("generation_changed"))
        else:
            ring = stream.ring if stream else deque()
            oldest = ring[0]["sequence"] if ring else last + 1
            if cursor[2] + 1 < oldest or cursor[2] > last:
                backlog.append(reset("expired"))
            else:
                backlog.extend(e for e in ring if e["sequence"] > cursor[2])
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._subs.setdefault(view.voice_session_id, set()).add(queue)
        return backlog, queue

    def detach(
        self, voice_session_id: str, queue: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        self._subs.get(voice_session_id, set()).discard(queue)

    @staticmethod
    def _snapshot(view: VoiceSessionView, last: int) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "event_id": str(uuid4()),
            "event": "voice.state",
            "control_session_id": view.control_session_id,
            "conversation_session_id": view.conversation_session_id,
            "voice_session_id": view.voice_session_id,
            "turn_id": None,
            "generation": view.generation,
            "sequence": last,
            "occurred_at_utc_ms": _now_ms(),
            "payload": {
                "state": view.state,
                "detail": view.detail,
                "retryable": view.retryable,
                "snapshot": view.model_dump(mode="json"),
            },
        }


# --- control ------------------------------------------------------------------


class VoiceControl:
    def __init__(
        self,
        *,
        runtime: LiveKitRuntime,
        registry: Callable[[], dict[str, Any]],
        platform_supported: Callable[[], bool] | None = None,
        poll_interval: float = 0.2,
        settings: Any = None,
    ) -> None:
        self.settings = settings
        self.runtime = runtime
        self._registry = registry
        self.host = EngineRuntimeHost(
            session_resolver=RegistryTargetResolver(registry, settings),
            runtime=runtime,
            platform_supported=platform_supported,
            join_ticket_signer=runtime,
            livekit_url=runtime.livekit_url,
        )
        self.router = EngineSessionRouter(
            runtime_host=self.host, session_lookup=RegistrySessionLookup(registry)
        )
        self.hub = VoiceEventHub()
        self.policy = SpeechOutputPolicy()
        self._poll = poll_interval
        self._disclosure: dict[str, tuple[bool, str | None]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._watching: set[str] = set()
        self._latency: dict[str, dict[str, int]] = {}
        # The engine issues voice capabilities; the daemon runs the turn. A revoke
        # here (stop, session switch, barge-in) is mirrored into the daemon.
        self._unlisten = add_voice_admission_revoke_listener(self._mirror_revoke)

    # --- control API ---

    def _decorate(self, view: VoiceSessionView) -> VoiceSessionView:
        remote, host = self._disclosure.get(view.voice_session_id, (False, None))
        return view.model_copy(update={"remote_llm": remote, "remote_llm_host": host})

    async def create(
        self, control_session_id: str, request: CreateVoiceSessionRequest
    ) -> VoiceSessionView:
        view = await self.host.create_session(OWNER_ID, control_session_id, request)
        if view.voice_session_id not in self._disclosure:
            session = self._registry().get(control_session_id)
            profile = getattr(session, "profile", None)
            if self.settings is not None:
                profile = self.settings.disclosure_profile(profile)
            self._disclosure[view.voice_session_id] = llm_disclosure(profile)
        if view.voice_session_id not in self._watching:
            self._watching.add(view.voice_session_id)
            self._spawn(self._watch(view.voice_session_id))
        return self._decorate(view)

    async def join(
        self, voice_session_id: str, request: JoinVoiceSessionRequest
    ) -> VoiceSessionTicket:
        return await self.host.issue_join_ticket(OWNER_ID, voice_session_id, request)

    async def stop(
        self, voice_session_id: str, request: StopVoiceSessionRequest
    ) -> VoiceSessionView:
        return self._decorate(
            await self.host.stop_session(OWNER_ID, voice_session_id, request)
        )

    async def status(self, voice_session_id: str) -> VoiceSessionView:
        view = await self.host.get_status(OWNER_ID, voice_session_id)
        assert isinstance(view, VoiceSessionView)
        return self._decorate(view)

    async def listener_opened(self, voice_session_id: str) -> None:
        """Tell the host an events stream opened (the session is in use)."""
        await self.host.listener_opened(voice_session_id)

    async def listener_closed(self, voice_session_id: str) -> None:
        """Tell the host an events stream closed (the grace starts at the disconnect)."""
        await self.host.listener_closed(voice_session_id)

    async def subscribe(
        self, voice_session_id: str, cursor_value: str | None
    ) -> AsyncIterator[dict[str, Any]]:
        """Checks run eagerly (before SSE headers); the returned iterator streams."""
        view = await self.status(voice_session_id)
        cursor = None
        if cursor_value:
            cursor = decode_cursor(cursor_value)
            if cursor[0] != voice_session_id:
                raise RuntimeHostError(
                    "cursor_mismatch",
                    409,
                    "The cursor belongs to another voice session.",
                )
        backlog, queue = self.hub.attach(view, cursor)

        async def stream() -> AsyncIterator[dict[str, Any]]:
            try:
                for envelope in backlog:
                    yield envelope
                if view.state in TERMINAL_STATES:
                    return
                while True:
                    envelope = await queue.get()
                    if envelope is None:
                        return
                    yield envelope
            finally:
                self.hub.detach(voice_session_id, queue)

        return stream()

    # --- worker routes ---

    async def open_turn(
        self,
        voice_session_id: str,
        snapshot: VoiceTranscriptSnapshot,
        *,
        kind: Literal["speech", "greeting"] = "speech",
        talk_over: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        """Admit a turn, then stream its reply as speech-ready sentences.

        Admission runs eagerly so a refusal answers with a plain HTTP error; the
        returned iterator yields {"text": sentence} lines and ends with
        {"done": true, "turn_id": ...}. Closing it before "done" is a barge-in.
        A greeting is a hidden instruction turn: no user bubble, no user transcript.
        """
        binding = await self.host.get_binding(OWNER_ID, voice_session_id)
        session = await self.router._get_session(
            binding
        )  # noqa: SLF001 - same lookup the router validates with
        if kind == "greeting":
            snapshot = snapshot.model_copy(update={"display_text": GREETING_INSTRUCTION})
        queue = session.subscribe()
        try:
            receipt = await self.router.admit_final(
                binding, snapshot, extra_context=self._turn_context(binding, kind, talk_over)
            )
        except BaseException:
            session.unsubscribe(queue)
            raise
        if receipt.duplicate or not receipt.accepted:
            session.unsubscribe(queue)
            return self._finished(snapshot.turn_id, duplicate=receipt.duplicate)
        if kind != "greeting":
            await self._emit(
                voice_session_id,
                snapshot.generation,
                "voice.transcript",
                None,
                turn_id=snapshot.turn_id,
                chunks=transcript_chunks(snapshot),
            )
        await self._emit(
            voice_session_id,
            snapshot.generation,
            "voice.state",
            {"state": "thinking"},
            turn_id=snapshot.turn_id,
        )
        return self._stream_turn(binding, snapshot, session, queue)

    def _turn_context(
        self, binding: VoiceSessionBinding, kind: str, talk_over: bool = False
    ) -> dict[str, Any]:
        """Voice-only turn settings the daemon applies for this turn and no other."""
        extra: dict[str, Any] = {}
        if self.settings is not None:
            extra["voice_prompt_overlay"] = self.settings.overlay(binding.agent_id)
            extra["voice_reasoning"] = self.settings.reasoning()
        if kind == "greeting":
            extra["voice_hidden"] = True
        if talk_over:
            extra["voice_talk_over"] = True  # a yes in it never answers the agent's ask
        return extra

    @staticmethod
    async def _finished(turn_id: str, *, duplicate: bool) -> AsyncIterator[dict[str, Any]]:
        yield {"done": True, "turn_id": turn_id, "duplicate": duplicate}

    async def record_interrupted_reply(
        self, voice_session_id: str, turn_id: str, text: str
    ) -> dict[str, Any]:
        """A talk-over cut a reply short: keep the part the worker says was played.

        An empty text is a talk-over before any of it was played: the reply goes.
        """
        binding = await self.host.get_binding(OWNER_ID, voice_session_id)
        text = text.strip()
        session = await self.router._get_session(binding)  # noqa: SLF001
        return await session.record_interrupted_reply(turn_id, text)

    async def ingest_event(self, voice_session_id: str, body: WorkerEvent) -> None:
        view = await self.status(voice_session_id)
        if body.generation is not None and body.generation != view.generation:
            raise RuntimeHostError(
                "stale_generation", 409, "Voice session generation is stale."
            )
        payload = dict(body.payload)
        if body.event == "voice.transcript":
            chunk = VoiceTranscriptChunk(**payload)
            if (
                chunk.speaker != "user"
                or chunk.final
                or chunk.voice_session_id != voice_session_id
            ):
                raise RuntimeHostError(
                    "invalid_request",
                    400,
                    "Workers post only partial user transcripts here.",
                )
            if (
                len(
                    (
                        chunk.display_text_chunk + (chunk.spoken_text_chunk or "")
                    ).encode()
                )
                > 4096
            ):
                raise RuntimeHostError(
                    "transcript_too_large", 413, "Transcript chunk is too large."
                )
            payload = chunk.model_dump(mode="json")
        elif body.event == "voice.state" and payload.get("state") == "downloading":
            # The web client keys model progress off downloading_models.
            payload = {
                "state": "downloading_models",
                "detail": payload.get("detail"),
                "download": {
                    k: payload.get(k) for k in ("model", "bytes_done", "bytes_total")
                },
            }
        elif body.event == "voice.metrics" and "stage" in payload:
            payload = self._merge_latency(body.turn_id, payload)
        self.hub.publish(view, body.event, payload, turn_id=body.turn_id)

    def _merge_latency(
        self, turn_id: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """The worker's stages for a turn: its own measures, the only writer.

        A turn's last audio is reported after its reply stream ended, so the
        stages outlive that stream; the newest turns are kept.
        """
        stages = self._latency.setdefault(turn_id or "", {})
        while len(self._latency) > 16:
            self._latency.pop(next(iter(self._latency)))
        key = {
            "stt": "stt_ms",
            "llm_first_token": "llm_first_token_ms",
            "first_audio": "first_audio_ms",
            "total_turn": "total_turn_ms",
        }.get(str(payload.get("stage")))
        if key and isinstance(payload.get("ms"), (int, float)) and payload["ms"] >= 0:
            stages[key] = int(payload["ms"])
        return self._metrics(stages)

    @staticmethod
    def _metrics(stages: dict[str, int]) -> dict[str, Any]:
        return {
            k: stages.get(k)
            for k in ("stt_ms", "llm_first_token_ms", "first_audio_ms", "total_turn_ms")
        }

    # --- internals ---

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _emit(
        self,
        voice_session_id: str,
        generation: int,
        event: str,
        payload: dict[str, Any] | None,
        *,
        turn_id: str | None = None,
        chunks: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Publish only while the generation is current; late callbacks vanish."""
        try:
            view = await self.status(voice_session_id)
        except RuntimeHostError:
            return False
        if view.generation != generation:
            return False
        for item in chunks if chunks is not None else [payload or {}]:
            self.hub.publish(view, event, item, turn_id=turn_id)
        return True

    async def _watch(self, voice_session_id: str) -> None:
        last: tuple[str, int, str | None] | None = None
        try:
            while True:
                try:
                    view = await self.status(voice_session_id)
                except RuntimeHostError:
                    return
                key = (view.state, view.generation, view.detail)
                if key != last:
                    last = key
                    self.hub.publish(
                        view,
                        "voice.state",
                        {
                            "state": view.state,
                            "detail": view.detail,
                            "retryable": view.retryable,
                        },
                    )
                if view.state in TERMINAL_STATES:
                    self.hub.close(voice_session_id)
                    return
                await asyncio.sleep(self._poll)
        finally:
            self._watching.discard(voice_session_id)

    def _mirror_revoke(
        self, voice_session_id: str, generation: int, turn_id: str | None
    ) -> None:
        """Registry listener: tell the daemon running the turn that it was revoked."""
        try:
            self._spawn(self._revoke_in_daemon(voice_session_id, generation, turn_id))
        except RuntimeError:  # no running loop: nothing is in flight to stop
            pass

    async def _revoke_in_daemon(
        self, voice_session_id: str, generation: int, turn_id: str | None
    ) -> None:
        try:
            binding = await self.host.get_binding(OWNER_ID, voice_session_id)
            session = await self.router._get_session(binding)  # noqa: SLF001
            revoke = getattr(session, "voice_admission", None)
            if revoke is None:
                return
            params = {"voice_session_id": voice_session_id, "generation": generation}
            if turn_id is None:
                await revoke("revoke_generation", **params)
            else:
                await revoke("revoke_turn", turn_id=turn_id, **params)
        except Exception:
            logger.warning("voice admission revoke did not reach the daemon", exc_info=True)

    def _barge_in(
        self, binding: VoiceSessionBinding, generation: int, turn_id: str, session: Any
    ) -> None:
        """The listener talked over the reply: stop the turn, let nothing late through.

        The /turns generator is the only publisher for its turn and it is gone,
        so the fence is that: the daemon's late events reach nobody.
        """
        revoke_voice_admissions_for_turn(binding.voice_session_id, generation, turn_id)
        session.cancel_turn(turn_id)  # only if it still runs: a later typed turn is not ours
        self._spawn(self._publish_barge_in(binding.voice_session_id, generation, turn_id))

    async def _publish_barge_in(
        self, voice_session_id: str, generation: int, turn_id: str
    ) -> None:
        await self._emit(
            voice_session_id,
            generation,
            "voice.state",
            {"state": "listening", "barge_in": True},
            turn_id=turn_id,
        )

    async def _stream_turn(
        self,
        binding: VoiceSessionBinding,
        snapshot: VoiceTranscriptSnapshot,
        session: Any,
        queue: asyncio.Queue,
    ) -> AsyncIterator[dict[str, Any]]:
        vsid, generation, turn_id = (
            binding.voice_session_id,
            snapshot.generation,
            snapshot.turn_id,
        )
        raw = ""  # the reply being streamed (one per model call in the turn)
        consumed = 0  # chars of its speakable text already cut into sentences
        said: list[str] = []  # sentences handed to the worker, across the turn
        revision = 0
        partial_id = str(uuid4())
        last_partial = 0.0
        completed = False
        after_response = False  # its display message repeats what "response" said

        async def emit(event: str, payload: dict[str, Any]) -> None:
            await self._emit(vsid, generation, event, payload, turn_id=turn_id)

        async def say(*, final: bool) -> AsyncIterator[dict[str, Any]]:
            """Sentences of the reply so far; the speakable text may still grow."""
            nonlocal consumed, revision
            speakable = spoken_so_far(raw)
            start = consumed
            if speakable is None:
                if not final:
                    return
                from kollabor_ai.response_channels import split_response_channels

                # Display-only reply: the visible text is what gets said.
                speakable, start = split_response_channels(raw)[0], 0
            pending = speakable[start:]
            sentences, rest = cut_sentences(pending, final=final)
            consumed += len(pending) - len(rest)
            for sentence in sentences:
                line = self.policy.sentence(sentence)
                if not line:
                    continue
                said.append(line)
                # voice states (thinking, speaking, listening) come from the worker,
                # which follows LiveKit's own; a sentence handed over here has not
                # been heard yet
                revision += 1
                await self._publish_assistant(
                    binding,
                    generation,
                    turn_id,
                    partial_id,
                    revision,
                    False,
                    raw,
                    " ".join(said),
                    partial=True,
                )
                yield {"text": line}

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), TURN_IDLE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    await emit(
                        "voice.error",
                        {
                            "code": "provider_error",
                            "message": "The assistant went quiet.",
                            "retryable": True,
                            "next_action": None,
                        },
                    )
                    completed = True
                    yield {"done": True, "turn_id": turn_id, "error": "provider_error"}
                    return
                kind = event.get("type")
                # Only this turn's events: a hub wake's or a typed turn's (no id)
                # stream to every subscriber, and would be said as this reply.
                if kind != "daemon_closed" and event.get("request_id") != turn_id:
                    continue
                if kind == "token":
                    after_response = False
                    raw += str(event.get("text") or "")
                    async for item in say(final=False):
                        yield item
                    now = time.monotonic()
                    if now - last_partial >= PARTIAL_INTERVAL_S:
                        last_partial = now
                        revision += 1
                        await self._publish_assistant(
                            binding,
                            generation,
                            turn_id,
                            partial_id,
                            revision,
                            False,
                            raw,
                            " ".join(said) or None,
                            partial=True,
                        )
                elif kind == "response" or (
                    kind == "message" and event.get("message_type") == "assistant"
                ):
                    # A model call's reply is shown (its display message), so its
                    # last sentence, with no space after it yet, is said now. Only
                    # a reply with XML tool tags sends "response" (its clean text)
                    # before that display message.
                    if kind == "message" and after_response:
                        after_response = False
                        continue
                    after_response = kind == "response"
                    reply = str(event.get("text") or event.get("content") or "")
                    raw = raw or reply  # no token events: the whole reply arrives here
                    async for item in say(final=True):
                        yield item
                    await self._final_response(binding, generation, turn_id, reply, said)
                    raw, consumed, revision, partial_id = "", 0, 0, str(uuid4())
                elif kind == "tool_start":
                    # A reply's display message comes before its tools: none is
                    # left to skip (a round of hub tags alone shows nothing).
                    after_response = False
                    await emit(
                        "voice.tool",
                        {
                            "state": "started",
                            "tool_id": str(event.get("tool_id") or ""),
                            "tool_name": str(event.get("tool_name") or ""),
                        },
                    )
                elif kind == "tool_result":
                    after_response = False  # as at tool_start: a denied call has only this
                    await emit(
                        "voice.tool",
                        {
                            "state": "completed",
                            "tool_id": str(event.get("tool_id") or ""),
                            "tool_name": str(event.get("tool_name") or ""),
                            "success": bool(event.get("success")),
                            "summary": "ok" if event.get("success") else "failed",
                        },
                    )
                elif kind == "voice_aside":
                    # The Quick Model's line for words said while a turn ran (#388).
                    # An answer ends this voice turn; a forward's words wait in the
                    # queue for their own reply, under the same turn id.
                    line = self.policy.sentence(str(event.get("text") or ""))
                    if line:
                        said.append(line)
                        # A stop's line also tells the worker to stop every turn
                        # still running there, with what it would still say.
                        stop = event.get("stop") is True
                        yield {"text": line, "stop": True} if stop else {"text": line}
                    if event.get("end_turn") is True:
                        await self._final_response(
                            binding, generation, turn_id, line, said
                        )
                        completed = True
                        yield {"done": True, "turn_id": turn_id}
                        return
                elif kind == "permission_request":
                    await emit(
                        "voice.permission",
                        {
                            "state": "pending",
                            "tool_id": str(event.get("tool_id") or ""),
                            "tool_name": str(event.get("tool_name") or ""),
                            "risk_level": str(event.get("risk_level") or ""),
                        },
                    )
                elif kind in (
                    "permission_granted",
                    "permission_denied",
                    "permission_cancelled",
                ):
                    await emit(
                        "voice.permission",
                        {
                            "state": kind.removeprefix("permission_"),
                            "tool_id": str(event.get("tool_id") or ""),
                        },
                    )
                elif kind == "error":
                    await emit(
                        "voice.error",
                        {
                            "code": "provider_error",
                            "message": "The assistant hit an error.",
                            "retryable": bool(event.get("retryable")),
                            "next_action": None,
                        },
                    )
                elif kind == "turn_complete":
                    if raw:  # its shown reply had no turn id (an ACP agent's): say the rest now
                        async for item in say(final=True):
                            yield item
                        await self._final_response(binding, generation, turn_id, raw, said)
                    completed = True
                    yield {"done": True, "turn_id": turn_id}
                    return
                elif kind == "daemon_closed":
                    await emit(
                        "voice.error",
                        {
                            "code": "runtime_unavailable",
                            "message": "The agent exited.",
                            "retryable": True,
                            "next_action": None,
                        },
                    )
                    completed = True
                    yield {"done": True, "turn_id": turn_id, "error": "runtime_unavailable"}
                    return
        except (asyncio.CancelledError, GeneratorExit):
            if not completed:
                self._barge_in(binding, generation, turn_id, session)
            raise
        except Exception:
            # A fault here, not a talk-over: the daemon's turn still stops, so it
            # never runs on with nobody listening. The worker sees the stream break.
            if not completed:
                revoke_voice_admissions_for_turn(vsid, generation, turn_id)
                session.cancel_turn(turn_id)  # only if it still runs
            raise
        finally:
            session.unsubscribe(queue)

    async def _publish_assistant(
        self,
        binding: VoiceSessionBinding,
        generation: int,
        turn_id: str,
        transcript_id: str,
        revision: int,
        final: bool,
        display: str,
        spoken: str | None,
        *,
        partial: bool = False,
    ) -> VoiceTranscriptSnapshot | None:
        from kollabor_ai.response_channels import display_response_text

        shown = display_response_text(display, partial=True) if partial else display
        snap = VoiceTranscriptSnapshot(
            transcript_id=transcript_id,
            voice_session_id=binding.voice_session_id,
            turn_id=turn_id,
            generation=generation,
            speaker="assistant",
            revision=revision,
            final=final,
            display_text=shown,
            spoken_text=spoken,
            language=None,
            observed_at_utc_ms=_now_ms(),
        )
        if len(shown.encode()) > MAX_TRANSCRIPT_BYTES:
            await self._emit(
                binding.voice_session_id,
                generation,
                "voice.error",
                {
                    "code": "transcript_too_large",
                    "message": "The reply is too long to show here.",
                    "retryable": False,
                    "next_action": None,
                },
                turn_id=turn_id,
            )
            return None
        ok = await self._emit(
            binding.voice_session_id,
            generation,
            "voice.transcript",
            None,
            turn_id=turn_id,
            chunks=transcript_chunks(snap),
        )
        return snap if ok else None

    async def _final_response(
        self,
        binding: VoiceSessionBinding,
        generation: int,
        turn_id: str,
        raw: str,
        said: list[str],
    ) -> None:
        """Publish the immutable final reply; its audio already went out as sentences."""
        from kollabor_ai.response_channels import split_response_channels

        display, spoken = split_response_channels(raw)
        if not display.strip() or display.strip() == ".":
            return  # voice rules: a lone period is a silent reply
        snap = VoiceTranscriptSnapshot(
            transcript_id=str(uuid4()),
            voice_session_id=binding.voice_session_id,
            turn_id=turn_id,
            generation=generation,
            speaker="assistant",
            revision=1,
            final=True,
            display_text=display,
            spoken_text=spoken,
            language=None,
            observed_at_utc_ms=_now_ms(),
        )
        decision = self.policy.prepare(snap, binding)
        await self._publish_assistant(
            binding,
            generation,
            turn_id,
            snap.transcript_id,
            1,
            True,
            display,
            decision.transcript.spoken_text,
        )

    async def shutdown(self) -> None:
        self._unlisten()
        try:
            await self.host.shutdown()
        except RuntimeHostError:
            logger.warning("voice runtime shutdown left sessions behind", exc_info=True)
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.runtime.shutdown()


__all__ = ["VoiceControl", "SpeechOutputPolicy", "WorkerEvent", "ValidationError"]
