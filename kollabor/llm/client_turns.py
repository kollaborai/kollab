"""FIFO admission and scoped cancellation for external ACP client turns.

Native processing remains the existing LLM/hook/tool pipeline. The broker
waits for its correlated terminal event, rather than treating admission as
completion or cancelling whatever happens to be running on the daemon.
"""
from __future__ import annotations

import asyncio
import queue
from typing import Any

from kollabor_tui.display_tap import publish_semantic
from kollabor_tui.turn_context import current_turn


class ClientTurnBroker:
    def __init__(self, llm: Any, bus: Any):
        self.llm = llm
        self.bus = bus
        self.pending: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10)
        self.cancelled: set[str] = set()
        self.active: str | None = None
        self.active_turn_id: int | None = None
        self.admitted: set[str] = set()
        self.turn_id = 0
        self.worker: asyncio.Task | None = None

    def submit(self, text: str, request_id: str) -> dict[str, Any]:
        self.turn_id += 1
        item = {"text": text, "request_id": request_id, "turn_id": self.turn_id}
        try:
            self.pending.put_nowait(item)
        except asyncio.QueueFull:
            return {"accepted": False, "reason": "client turn queue is full"}
        self.admitted.add(request_id)
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._run(), name="acp-client-turns")
        return {"accepted": True, "request_id": request_id, "turn_id": self.turn_id}

    async def _run(self) -> None:
        from kollabor.user_input_source import UserInputSource
        tap = self.bus.get_service("display_tap")
        subscription = f"acp-client-owner-{id(self)}"
        events = tap.subscribe(subscription)
        try:
            while not self.pending.empty():
                item = await self.pending.get()
                request_id = item["request_id"]
                while self.llm.is_processing and request_id not in self.cancelled:
                    await asyncio.sleep(0.05)
                if request_id in self.cancelled:
                    self.cancelled.discard(request_id)
                    self.admitted.discard(request_id)
                    publish_semantic(self.bus, "turn_complete", request_id=request_id,
                                     turn_id=item["turn_id"], stop_reason="cancelled")
                    continue
                self.active = request_id
                self.active_turn_id = item["turn_id"]
                fields = {"request_id": request_id, "turn_id": item["turn_id"], "input_source": "acp_client"}
                token = current_turn.set(fields)
                try:
                    publish_semantic(self.bus, "turn_start", **fields)
                    result = await self.llm.submit_human_input(
                        item["text"], source=UserInputSource.STATE_RPC,
                        request_id=request_id, origin_context={"operator": True, "acp_client": True},
                    )
                    if result.get("status") in {"rejected", "cancelled"}:
                        publish_semantic(self.bus, "turn_complete", **fields, stop_reason="cancelled",
                                         error=result.get("reason", "input was refused"))
                    else:
                        while request_id not in self.cancelled:
                            try:
                                event = events.get_nowait()
                            except queue.Empty:
                                await asyncio.sleep(0.01)
                                continue
                            if event.get("request_id") == request_id and event.get("type") == "turn_complete":
                                break
                except asyncio.CancelledError:
                    publish_semantic(self.bus, "turn_complete", **fields, stop_reason="cancelled")
                    raise
                except Exception as exc:
                    publish_semantic(self.bus, "turn_complete", **fields, stop_reason="error",
                                     error=f"native client turn failed ({type(exc).__name__})")
                finally:
                    current_turn.reset(token)
                    self.active = None
                    self.active_turn_id = None
                    self.admitted.discard(request_id)
                    self.cancelled.discard(request_id)
        finally:
            tap.unsubscribe(subscription)

    async def cancel(self, request_id: str) -> dict[str, Any]:
        if request_id not in self.admitted:
            return {"cancelled": False, "request_id": request_id}
        self.cancelled.add(request_id)
        if self.active == request_id:
            self.llm.cancel_current_request()
            publish_semantic(self.bus, "turn_complete", request_id=request_id,
                             turn_id=self.active_turn_id, stop_reason="cancelled")
        return {"cancelled": True, "request_id": request_id}

    async def close(self) -> None:
        if self.active:
            await self.cancel(self.active)
        if self.worker and not self.worker.done():
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        while not self.pending.empty():
            item = self.pending.get_nowait()
            publish_semantic(self.bus, "turn_complete", request_id=item["request_id"],
                             turn_id=item["turn_id"], stop_reason="cancelled")
        self.admitted.clear()
        self.cancelled.clear()
