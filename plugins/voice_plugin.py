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
from datetime import datetime

from kollabor_events import EventType, Hook, HookPriority
from kollabor_plugins.base import BasePlugin

logger = logging.getLogger(__name__)


class VoicePlugin(BasePlugin):
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
        self._allow_stale = False
        self._admissions = None
        self._admission_lock = asyncio.Lock()
        self._remote_binding = None
        self._remote_jobs = {}
        self._remote_heartbeat_task = None
        self._routing_error = None
        from kollabor_voice.classifiers import load_classifier, load_context_lines

        self.classifier_name = load_classifier()
        self.context_lines = load_context_lines()
        self._classifier_generation = 0
        self._last_decision = None
        self._decision_deferred_ids = []
        self._speech_api = None
        self._speech_api_profile = None
        self._speech_api_lock = asyncio.Lock()
        self._output_generation = 0
        self._seen_speech = {}
        self._last_speech_decision = None
        self._speech_review_state = None
        self._remote_reply_queue = asyncio.Queue(maxsize=32)
        self._remote_reply_task = None

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
        state = dict(self._state)
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
        )
        return state

    async def set_classifier(self, name):
        from kollabor_voice.classifiers import CLASSIFIERS, save_classifier

        if name not in CLASSIFIERS:
            raise ValueError("Choose laya or provider")
        save_classifier(name)
        self.classifier_name = name
        self._classifier_generation += 1
        self._last_decision = None
        self._observer_error = self._routing_error
        self._allow_stale = True
        if name == "laya" and self._client and self.requested:
            await self._client.call("classifier_prepare")
        self._retry.set()
        self._refresh()
        return f"Voice classifier: {CLASSIFIERS[name]}. Saved for this device; pending speech is preserved."

    def set_context_lines(self, value):
        from kollabor_voice.classifiers import save_context_lines

        save_context_lines(value)
        self.context_lines = value
        self._classifier_generation += 1
        self._refresh()
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
        self._refresh()
        return "Voice Off"

    async def retry_voice(self):
        if self.classifier_name == "laya" and self._client and self.requested:
            await self._client.call("classifier_prepare")
        if (
            self.requested
            and self._lease
            and self._observer_error
            and not self._routing_error
        ):
            self._observer_error = None
            self._allow_stale = True  # Explicit retry is the user's review action.
            self._retry.set()
            return "Voice retrying pending speech"
        await self.stop_voice()
        return await self.start_voice()

    def _auth(self):
        if not self._lease:
            raise RuntimeError("Voice is off")
        return {"token": self._lease["token"], "epoch": self._lease["epoch"]}

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
            if initial.get("state") == "error":
                initial = await self._client.call("retry")
            if self.classifier_name == "laya":
                await self._client.call("classifier_prepare")
                initial = await self._client.call("status")
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
                if initial.get("state") == "error":
                    raise RuntimeError(
                        initial.get("error")
                        or initial.get("detail")
                        or "Voice setup failed"
                    )
                await asyncio.sleep(0.3)
                initial = await self._client.call("status")
            if not self.requested:
                return
            self._claim_id = uuid.uuid4().hex
            self._lease = await self._client.call(
                "claim",
                client_id=self.client_id,
                activated_at=activated,
                claim_id=self._claim_id,
            )
            if self.classifier_name == "laya":
                await self._client.call("classifier_prepare")
            cursor = self._lease["cursor"]
            rpc = self._event_bus.get_service("rpc_client")
            if rpc:
                await rpc.call(
                    "voice.bind",
                    {"epoch": self._lease["epoch"], "client_id": self.client_id},
                    timeout=2,
                )
                self._remote_heartbeat_task = asyncio.create_task(
                    self._keep_remote_alive(rpc), name="voice-routing-heartbeat"
                )
            self._observer = asyncio.create_task(
                self._observe_loop(), name="voice-observer"
            )
            while self.requested:
                self._state = await self._client.call("heartbeat", **self._auth())
                if len(self._pending) < 32:
                    result = await self._client.call(
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
                else:
                    self._observer_error = (
                        "Voice observation is behind; speech remains in the transcript. "
                        "Run /voicemode retry"
                    )
                self._refresh()
                await asyncio.sleep(0.35)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state = (
                "active_elsewhere"
                if isinstance(exc, VoiceError)
                and exc.code in {"not_owner", "superseded"}
                else "disconnected"
            )
            self._state = {"state": state, "error": str(exc)}
            self.requested = False
            if self._observer:
                self._observer.cancel()
            self._refresh()
            logger.warning("Voice client stopped: %s", exc)

    async def _keep_remote_alive(self, rpc):
        try:
            while self.requested:
                await rpc.call(
                    "voice.heartbeat", {"epoch": self._lease["epoch"]}, timeout=2.5
                )
                await asyncio.sleep(0.35)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A remote AI outage must not close the local microphone or prevent
            # durable transcription. Routing pauses with pending speech visible.
            self._routing_error = str(exc)
            self._observer_error = (
                f"AI connection lost; transcription continues. /voicemode retry ({exc})"
            )
            self._refresh()

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
        while self.requested:
            if not self._pending:
                await asyncio.sleep(0.15)
                continue
            if self._observer_error:
                await self._retry.wait()
                self._retry.clear()
                continue
            if self.classifier_name == "laya":
                classifier = self._state.get("classifier", {})
                if classifier.get("state") == "error":
                    self._observer_error = classifier.get(
                        "detail", "Laya failed; /voicemode retry"
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
            try:
                self._observing = True
                newest = datetime.fromisoformat(records[-1]["ended_at"]).timestamp()
                if not self._allow_stale and time.time() - newest > 30:
                    raise RuntimeError(
                        "Speech is older than 30 seconds and awaits review; /voicemode retry retries it explicitly"
                    )
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
                self._decision_deferred_ids = []
                selected = await asyncio.wait_for(self.observe_records(candidates), 10)
                if classifier_generation != self._classifier_generation:
                    continue
                selected = connected_speech(candidates, selected)
                # If more of the utterance arrived during inference, decide on
                # the larger batch before admitting any fragment to the host.
                if self._state.get("speech_active") or len(self._pending) > len(
                    records
                ):
                    if len(records) < 8:
                        continue
                if not self.requested or epoch != self._lease["epoch"]:
                    return
                # Recheck actual device ownership after inference, before dispatch.
                await self._client.call("heartbeat", **self._auth())
                if selected:
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
                            "uncertain_event_ids": list(self._decision_deferred_ids),
                            # An earlier ignored utterance in this same batch
                            # informed classification too. Keep it for the host
                            # without replaying it as a fresh request.
                            "transcript_context": self._transcript_context_for(
                                selected, candidates
                            ),
                            "playback_context": playback_text(selected),
                            "classifier": self._last_decision,
                            "expires_at": (time.time() if self._allow_stale else newest)
                            + 30,
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
                del self._pending[: len(records)]
                self._remember_transcripts(records)
                chosen = {r["event_id"] for r in selected}
                self._recent_ignored = [
                    r for r in candidates if r["event_id"] not in chosen
                ][-8:]
                self._allow_stale = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if classifier_generation != self._classifier_generation:
                    continue
                self._observer_error = (
                    str(exc) or "Voice observer timed out; /voicemode retry"
                )
                logger.warning("Voice observation pending: %s", self._observer_error)
            finally:
                self._observing = False
            self._refresh()

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
        decision = await classifier.decide(
            DecisionRequest(
                records, context, self._decision_transcript_context, self.context_lines
            )
        )
        self._last_decision = decision.to_wire()
        self._decision_deferred_ids = decision.deferred_event_ids
        return decision.selected(records)

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
                if len(recent) == 2:
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
                raise RuntimeError(
                    "Voice request expired before admission; review pending speech with /voicemode retry"
                )
            await self.validate_turn(voice)
            if self._admissions is None:
                self._admissions = AdmissionStore(service_root() / "admissions.sqlite3")
            previous = self._admissions.reserve(voice["admission_id"], data["message"])
            if previous:
                return previous
            # If submit/commit is interrupted, delivery_unknown survives on disk.
            result = await submit()
            self._admissions.finish(voice["admission_id"], result["status"])
            return {**result, "admission_id": voice["admission_id"]}

    async def voice_out(
        self, text, reply_id, owner_epoch, producer_id, rewrite_count=0
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
                    raise RuntimeError(f"Still unsuitable after one rewrite: {reason}")
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
        return {"bound": True}

    def _check_remote(self, params):
        if (
            not self._remote_binding
            or params.get("epoch") != self._remote_binding["epoch"]
            or time.monotonic() - self._remote_binding["heartbeat"] >= 3
        ):
            raise RuntimeError("Remote voice lease expired")

    async def _remote_heartbeat(self, params):
        self._check_remote(params)
        self._remote_binding["heartbeat"] = time.monotonic()
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
