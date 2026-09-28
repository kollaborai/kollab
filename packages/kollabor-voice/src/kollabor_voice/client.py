"""Small async client. Agent processes never import models or audio libraries."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .control import (
    MAX_REQUEST,
    PROTOCOL,
    VoiceError,
    prepare_root,
    service_root,
    socket_path,
)


class VoiceClient:
    def __init__(self, root: Path | None = None):
        self.root = root or service_root()

    async def call(self, op: str, **params) -> dict:
        async def exchange():
            reader, writer = await asyncio.open_unix_connection(
                str(socket_path(self.root)), limit=MAX_REQUEST
            )
            try:
                payload = (
                    json.dumps({"protocol": PROTOCOL, "op": op, **params}).encode()
                    + b"\n"
                )
                if len(payload) > MAX_REQUEST:
                    raise VoiceError(
                        "too_large", "Voice request exceeds the size limit"
                    )
                writer.write(payload)
                await writer.drain()
                response = json.loads(await reader.readline())
                if response.get("protocol") != PROTOCOL:
                    raise VoiceError(
                        "incompatible",
                        "Voice service version differs; restart the voice service after updating Kollab",
                    )
                if not response.get("ok"):
                    raise VoiceError(
                        response.get("code", "service_error"),
                        response.get("error", "Voice service failed"),
                    )
                return response["result"]
            finally:
                writer.close()
                await writer.wait_closed()

        return await asyncio.wait_for(exchange(), 2.0)

    def launch(self) -> None:
        if os.name != "posix":
            raise VoiceError(
                "unsupported",
                "The local voice service currently requires macOS or Linux",
            )
        prepare_root(self.root)
        logs = self.root / "logs"
        logs.mkdir(exist_ok=True)
        # Pass the installed package directory, not a repository-relative path.
        # This works for wheels and editable installs without installing Kollab
        # or its AI credentials into the isolated audio environment.
        # The audio service needs OS/runtime settings, never provider credentials.
        allowed = {
            "HOME",
            "PATH",
            "TMPDIR",
            "LANG",
            "LC_ALL",
            "USER",
            "LOGNAME",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "UV_CACHE_DIR",
        }
        env = {key: value for key, value in os.environ.items() if key in allowed}
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        with (logs / "service.log").open("ab") as log:
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "kollabor_voice.bootstrap",
                    "--root",
                    str(self.root),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                env=env,
                close_fds=True,
            )

    def setup_status(self) -> dict:
        try:
            data = json.loads((self.root / "setup.json").read_text())
            try:
                os.kill(int(data["pid"]), 0)
            except ProcessLookupError:
                return {
                    **data,
                    "state": "error",
                    "detail": "Voice setup stopped; run /voicemode retry",
                }
            if time.time() - data["updated_at"] > 960:
                return {
                    "state": "error",
                    "detail": "Voice setup stopped; run /voicemode retry",
                }
            return data
        except (OSError, ValueError, KeyError):
            return {"state": "starting", "detail": "Checking local voice service"}

    async def ensure(self, progress) -> dict:
        try:
            return await self.call("status")
        except (OSError, asyncio.TimeoutError, ValueError):
            pass
        self.launch()
        # Installation is cancellable from the client. The singleton can finish
        # its cache independently, but only an explicit claim can open the mic.
        launched = time.time()
        while time.time() - launched < 1000:
            try:
                return await self.call("status")
            except (OSError, asyncio.TimeoutError, ValueError):
                state = self.setup_status()
                progress(state)
                if (
                    state.get("state") == "error"
                    and state.get("updated_at", 0) >= launched
                ):
                    raise VoiceError(
                        "setup_failed", state.get("detail", "Voice setup failed")
                    )
                await asyncio.sleep(0.3)
        raise VoiceError("setup_timeout", "Voice setup timed out; run /voicemode retry")

    async def voice_out(
        self,
        text: str,
        reply_id: str,
        owner_epoch: str,
        producer_id: str,
        token: str,
        review: dict | None = None,
    ):
        """Agent adapter. Use an explicitly delegated capability; never claim capture."""
        from .speech_review import SpeechDecision, format_decision

        decision = format_decision(text) or (
            SpeechDecision.from_wire(review)
            if review is not None
            else await self.review_speech(text, owner_epoch, token)
        )
        if decision.decision != "speak":
            return {
                "status": decision.decision,
                "review": decision.to_wire(),
                "ids": [],
            }
        return await self.call(
            "voice_out",
            text=text,
            reply_id=reply_id,
            epoch=owner_epoch,
            producer_id=producer_id,
            token=token,
        )

    async def review_speech(self, text, epoch, token):
        from .speech_review import SpeechDecision, format_decision

        fixed = format_decision(text)
        if fixed:
            return fixed
        ticket = await self.call(
            "speech_review_submit", text=text, epoch=epoch, token=token
        )
        async with asyncio.timeout(10):
            while True:
                result = await self.call(
                    "speech_review_result",
                    job_id=ticket["job_id"],
                    epoch=epoch,
                    token=token,
                )
                if result["done"]:
                    return SpeechDecision.from_wire(result["result"])
                await asyncio.sleep(0.05)
