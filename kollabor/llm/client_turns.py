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
        self.active: str | None = None
        self.active_turn_id: int | None = None
        self.active_completion: dict[str, Any] | None = None
        self.submitting: asyncio.Task | None = None
        self.waiting: dict[str, Any] | None = None
        self.admitted: set[str] = set()
        self.turn_id = 0
        self.worker: asyncio.Task | None = None

    def submit(self, text: str, request_id: str) -> dict[str, Any]:
        if request_id in self.admitted:
            return {"accepted": False, "reason": "client request is already admitted"}
        waiting_count = int(self.waiting is not None and self.waiting["request_id"] in self.admitted)
        if self.pending.qsize() + waiting_count >= 10:
            return {"accepted": False, "reason": "client turn queue is full"}
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

    def _settle(self, item: dict[str, Any], *, stop_reason: str, error: str = "",
                completion: dict[str, Any] | None = None) -> None:
        request_id = item["request_id"]
        if request_id not in self.admitted:
            return
        self.admitted.discard(request_id)
        publish_semantic(self.bus, "turn_complete", request_id=request_id,
                         turn_id=item["turn_id"], stop_reason=stop_reason,
                         error=error, input_source="acp_client", queued=completion is None,
                         _client_completion=completion)

    async def _run(self) -> None:
        from kollabor.user_input_source import UserInputSource
        tap = self.bus.get_service("display_tap")
        subscription = f"acp-client-owner-{id(self)}"
        events = tap.subscribe(subscription)
        try:
            while not self.pending.empty():
                item = await self.pending.get()
                self.waiting = item
                request_id = item["request_id"]
                try:
                    while self.llm.is_processing and request_id in self.admitted:
                        await asyncio.sleep(0.05)
                except asyncio.CancelledError:
                    self._settle(item, stop_reason="cancelled")
                    raise
                finally:
                    self.waiting = None
                    self.pending.task_done()
                if request_id not in self.admitted:
                    continue
                self.active = request_id
                self.active_turn_id = item["turn_id"]
                completion = {"request_id": request_id, "turn_id": item["turn_id"],
                              "completed": False, "cancelled": False}
                self.active_completion = completion
                fields = {"request_id": request_id, "turn_id": item["turn_id"],
                          "input_source": "acp_client", "_client_completion": completion}
                token = current_turn.set(fields)
                try:
                    publish_semantic(self.bus, "turn_start", **fields)
                    self.submitting = asyncio.create_task(self.llm.submit_human_input(
                        item["text"], source=UserInputSource.STATE_RPC,
                        request_id=request_id, origin_context={"operator": True, "acp_client": True},
                    ), name=f"acp-client-submit-{request_id}")
                    result = await self.submitting
                    if result.get("status") in {"rejected", "cancelled"}:
                        self._settle(item, stop_reason="cancelled", completion=completion,
                                     error=result.get("reason", "input was refused"))
                    else:
                        while not completion["completed"]:
                            try:
                                event = events.get_nowait()
                            except queue.Empty:
                                await asyncio.sleep(0.01)
                                continue
                            if event.get("request_id") == request_id and event.get("type") == "turn_complete":
                                break
                except asyncio.CancelledError:
                    self._settle(item, stop_reason="cancelled", completion=completion)
                    if not completion["cancelled"] or asyncio.current_task().cancelling():
                        raise
                except Exception as exc:
                    self._settle(item, stop_reason="error", completion=completion,
                                 error=f"native client turn failed ({type(exc).__name__})")
                finally:
                    current_turn.reset(token)
                    self.active = None
                    self.active_turn_id = None
                    self.active_completion = None
                    self.submitting = None
                    self.admitted.discard(request_id)
        finally:
            tap.unsubscribe(subscription)

    async def cancel(self, request_id: str) -> dict[str, Any]:
        if request_id not in self.admitted:
            return {"cancelled": False, "request_id": request_id}
        if self.active == request_id:
            completion = self.active_completion
            if completion is None or completion["completed"]:
                return {"cancelled": False, "request_id": request_id}
            completion["cancelled"] = True
            if self.submitting is not None and not self.submitting.done():
                self.submitting.cancel()
            self.llm.cancel_current_request()
            self._settle({"request_id": request_id, "turn_id": self.active_turn_id},
                         stop_reason="cancelled", completion=completion)
        elif self.waiting and self.waiting["request_id"] == request_id:
            self._settle(self.waiting, stop_reason="cancelled")
        else:
            for item in tuple(self.pending._queue):
                if item["request_id"] == request_id:
                    self.pending._queue.remove(item)
                    self.pending.task_done()
                    self._settle(item, stop_reason="cancelled")
                    break
        return {"cancelled": True, "request_id": request_id}

    async def close(self) -> None:
        if self.active:
            await self.cancel(self.active)
        if self.worker and not self.worker.done():
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        while not self.pending.empty():
            item = self.pending.get_nowait()
            self.pending.task_done()
            self._settle(item, stop_reason="cancelled")
        self.admitted.clear()
