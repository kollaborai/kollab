"""Turn runner - executes one full LLM turn and emits SSE events."""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Dict, List

from . import sse
from .session import EngineSession

logger = logging.getLogger(__name__)

MAX_AGENTIC_TURNS = 20  # prevent runaway loops


def _tc_get(tool_call: Any, key: str, default: Any = None) -> Any:
    """Get a field from a tool_call that may be a dict or a ToolUseContent object."""
    if isinstance(tool_call, dict):
        return tool_call.get(key, default)
    return getattr(tool_call, key, default)


class TurnRunner:
    """
    Runs a full multi-turn LLM conversation loop for one user message.
    Produces SSE events via asyncio.Queue.

    The pattern:
      1. Add user message to history
      2. Call LLM (streaming tokens → SSE)
      3. Parse tool calls from response
      4. Execute each tool (permission check may pause → SSE)
      5. If tools were called: loop back so the model can consume results
      6. Otherwise: turn_complete
    """

    async def run(
        self, session: EngineSession, message: str
    ) -> AsyncGenerator[Dict, None]:
        """
        Run a turn. Yields SSE event dicts.
        Uses an internal queue so producer (turn logic) and consumer (SSE)
        can run concurrently via asyncio.
        """
        queue: asyncio.Queue = asyncio.Queue()

        # Wire the queue into the session so permission_callback can emit to it
        session._sse_queue = queue

        # Run turn logic as a background task
        task = asyncio.create_task(self._run_turn(session, message, queue))
        session._active_turn_task = task

        try:
            while True:
                event = await queue.get()
                if event is None:  # sentinel - turn finished
                    break
                yield event
        finally:
            session._sse_queue = None
            session._active_turn_task = None
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _run_turn(
        self,
        session: EngineSession,
        message: str,
        queue: asyncio.Queue,
    ) -> None:
        """Producer: runs the full turn loop, puts events to queue."""
        total_input_tokens = 0
        total_output_tokens = 0
        total_tool_calls = 0
        stop_reason = "end_turn"
        all_tool_results: List[Dict] = []
        final_response_text = ""

        try:
            # Add user message to history
            session.history.append({"role": "user", "content": message})

            for turn_num in range(MAX_AGENTIC_TURNS):
                logger.debug(
                    f"Session {session.session_id}: agentic turn {turn_num + 1}"
                )

                # Build streaming token callback
                async def token_callback(chunk: str):
                    await queue.put(sse.token(session.session_id, chunk))

                # Get available tools
                tools = await session.get_tools()

                logger.debug(
                    "Session %s: calling LLM (provider=%s model=%s "
                    "history=%s tools=%s)",
                    session.session_id,
                    session.profile.provider,
                    session.profile.get_model(),
                    len(session.history),
                    len(tools) if tools else 0,
                )

                # Call LLM (streaming)
                response_text = await session.api_service.call_llm(
                    conversation_history=session.history,
                    streaming_callback=token_callback,
                    tools=tools,
                )

                # Emit thinking content if present
                if session.api_service.last_thinking_content:
                    await queue.put(
                        sse.thinking(
                            session.session_id,
                            session.api_service.last_thinking_content,
                        )
                    )

                # Accumulate token usage
                usage = session.api_service.last_token_usage
                total_input_tokens += usage.get("prompt_tokens", 0)
                total_output_tokens += usage.get("completion_tokens", 0)

                stop_reason = session.api_service.last_stop_reason or "end_turn"
                tool_calls = session.api_service.last_tool_calls or []
                if response_text.strip():
                    final_response_text = response_text

                # Normalise tool_calls to dicts once so all downstream code is safe.
                # Also resolve the executor tool_type: the tool_executor routes based
                # on "type" being "mcp_tool", "terminal", "file_*", etc. - not the
                # LLM's "tool_use". MCP names route to MCP; built-in registry native
                # names route to their native executor type (file_read, terminal, etc.).
                mcp_tool_names = set(session.mcp_integration.tool_registry.keys())
                registry_native_names: set[str] = set()
                try:
                    from kollabor_agent.tool_registry import get_registry

                    registry_native_names = {
                        tool.native_name for tool in get_registry().list()
                    }
                except Exception:
                    logger.debug("Tool registry unavailable during tool normalisation")

                def _normalise_tc(tc: Any) -> dict:
                    if isinstance(tc, dict):
                        name = tc.get("name", "")
                        raw_type = tc.get("type", "tool_use")
                        input_val = tc.get("input") or tc.get("arguments") or {}
                    else:
                        name = getattr(tc, "name", "")
                        raw_type = getattr(tc, "type", "tool_use")
                        input_val = getattr(tc, "input", {})
                    # Resolve executor type
                    if name in mcp_tool_names:
                        resolved_type = "mcp_tool"
                    elif name in registry_native_names:
                        resolved_type = name
                    elif raw_type not in ("tool_use",):
                        resolved_type = raw_type  # already classified (terminal, file_*, etc.)
                    else:
                        resolved_type = "mcp_tool"  # default: treat as MCP tool

                    base = {
                        "type": resolved_type,
                        "id": _tc_get(tc, "id", ""),
                        "name": name,
                        "input": input_val,
                        "arguments": input_val,
                    }
                    if resolved_type != "mcp_tool" and isinstance(input_val, dict):
                        base.update(input_val)
                    if isinstance(tc, dict):
                        return {
                            **tc,
                            **base,
                        }
                    return base

                tool_calls = [_normalise_tc(tc) for tc in tool_calls]

                # Add assistant response to history using the same metadata pattern
                # as queue_processor.py — provider's _prepare_request() handles
                # the per-provider conversion (anthropic ↔ openai format).
                assistant_msg: dict = {"role": "assistant", "content": response_text}
                if tool_calls:
                    import json as _json

                    assistant_msg["metadata"] = {
                        "tool_calls": [
                            {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": tc.get("name", ""),
                                    "arguments": _json.dumps(tc.get("input", {})),
                                },
                            }
                            for tc in tool_calls
                        ]
                    }
                session.history.append(assistant_msg)

                # No tool calls → turn done
                if not tool_calls:
                    break

                # Execute each tool
                tool_results = []
                for tool_call in tool_calls:
                    result = await self._execute_tool(session, tool_call, queue)
                    tool_results.append(result)
                    total_tool_calls += 1

                # Add tool results to history
                self._add_tool_results_to_history(session, tool_results)

                # Track all tool results across the turn for fallback synthesis
                all_tool_results.extend(tool_results)

                # Any actual tool call requires another model pass so the
                # assistant can see the tool results before completing.
                continue


            # Turn complete
            # If the model ran tools but never produced visible text, build a
            # clean response from the tool results so the user sees something.
            if not final_response_text and all_tool_results:
                import json as _json
                summaries = []
                for tr in all_tool_results:
                    if not tr["result"].success:
                        continue
                    raw = tr["result"].output or ""
                    # Try to pretty-print JSON results
                    try:
                        data = _json.loads(raw)
                        # Format common patterns
                        if isinstance(data, dict):
                            if "chains" in data:
                                items = data["chains"]
                                if items:
                                    names = [c.get("name", c.get("id", "?")) for c in items[:20]]
                                    summaries.append(f"chains ({len(items)} total): {', '.join(names)}")
                                else:
                                    summaries.append("no chains found")
                            elif "agents" in data:
                                items = data["agents"]
                                if items:
                                    names = [a.get("name", a.get("id", "?")) for a in items[:20]]
                                    summaries.append(f"agents ({len(items)} total): {', '.join(names)}")
                                else:
                                    summaries.append("no agents found")
                            elif "tasks" in data:
                                items = data["tasks"]
                                summaries.append(f"{len(items)} open tasks" if items else "no open tasks")
                            elif "workspace" in data:
                                w = data["workspace"]
                                summaries.append(f"active workspace: {w.get('name', '?')} ({w.get('path', '?')})")
                            else:
                                # generic — just confirm it ran
                                summaries.append(raw[:200])
                        else:
                            summaries.append(raw[:200])
                    except Exception:
                        summaries.append(raw[:200])
                if summaries:
                    await queue.put(sse.token(session.session_id, "\n\n".join(summaries)))

            session.total_turns += 1
            session.total_input_tokens += total_input_tokens
            session.total_output_tokens += total_output_tokens

            await queue.put(
                sse.turn_complete(
                    session_id=session.session_id,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    tool_calls=total_tool_calls,
                    stop_reason=stop_reason,
                )
            )

        except asyncio.CancelledError:
            await queue.put(
                sse.error(
                    session_id=session.session_id,
                    code="cancelled",
                    message="Turn cancelled by user",
                )
            )
        except Exception as e:
            logger.exception(f"Session {session.session_id}: turn error: {e}")
            code = _classify_error(e)

            # Include full error details for debugging
            error_msg = str(e)
            error_type = type(e).__name__

            # Build detailed error message for SSE
            sse_msg = f"{error_type}: {error_msg}"
            if hasattr(e, "provider"):
                sse_msg += f" [provider={e.provider}]"
            if hasattr(e, "error_code"):
                sse_msg += f" [code={e.error_code}]"
            if hasattr(e, "original_error"):
                sse_msg += f" [original={type(e.original_error).__name__}: {str(e.original_error)}]"

            import sys

            print(f"[ENGINE-ERROR] {sse_msg}", file=sys.stderr)
            sys.stderr.flush()

            await queue.put(
                sse.error(
                    session_id=session.session_id,
                    code=code,
                    message=sse_msg,
                    retryable=code in ("rate_limit", "timeout"),
                )
            )
        finally:
            await queue.put(None)  # sentinel

    async def _execute_tool(
        self,
        session: EngineSession,
        tool_call: Dict[str, Any],
        queue: asyncio.Queue,
    ) -> Dict[str, Any]:
        """Execute one tool call, emitting tool_start and tool_result events."""
        # Normalise ToolUseContent objects (Pydantic) to plain dicts so all
        # downstream code (tool_executor, history building) can use .get()
        if not isinstance(tool_call, dict):
            tool_call = {
                "type": getattr(tool_call, "type", "tool_use"),
                "id": getattr(tool_call, "id", ""),
                "name": getattr(tool_call, "name", ""),
                "input": getattr(tool_call, "input", {}),
            }
        tool_id = _tc_get(tool_call, "id", "")
        tool_name = _tc_get(tool_call, "name") or _tc_get(tool_call, "type", "unknown")
        tool_type = _tc_get(tool_call, "type", "unknown")
        tool_input = _tc_get(tool_call, "input") or _tc_get(tool_call, "arguments", {})

        await queue.put(
            sse.tool_start(
                session_id=session.session_id,
                tool_id=tool_id,
                tool_name=tool_name,
                tool_type=tool_type,
                input=tool_input,
            )
        )

        start = time.monotonic()
        result = await session.tool_executor.execute_tool(tool_call)
        elapsed = time.monotonic() - start

        await queue.put(
            sse.tool_result(
                session_id=session.session_id,
                tool_id=tool_id,
                tool_name=tool_name,
                success=result.success,
                output=result.output,
                error=result.error,
                execution_time=elapsed,
                metadata=result.metadata,
            )
        )

        return {
            "tool_id": tool_id,
            "tool_call": tool_call,
            "result": result,
        }

    def _add_tool_results_to_history(
        self,
        session: EngineSession,
        tool_results: List[Dict],
    ) -> None:
        """Add tool results to history using the same pattern as queue_processor.py.

        Uses api_service.format_tool_result() which returns role='tool' messages
        (OpenAI format). The provider's _prepare_request() then converts to
        whatever format the target provider needs (e.g. anthropic_provider converts
        role='tool' back to tool_result content blocks for the Anthropic API).
        """
        if not tool_results:
            return

        for tr in tool_results:
            tool_call = tr["tool_call"]
            result = tr["result"]
            tool_id = _tc_get(tool_call, "id", "")
            output = result.output if result.success else f"Error: {result.error}"
            msg = session.api_service.format_tool_result(
                tool_id, output, is_error=not result.success
            )
            # Preserve tool_call_id in metadata so _prepare_messages passes it through
            msg["metadata"] = {"tool_call_id": tool_id}
            session.history.append(msg)


def _classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "rate limit" in msg or "429" in msg:
        return "rate_limit"
    if "auth" in msg or "401" in msg or "403" in msg:
        return "auth_error"
    if "context" in msg and ("length" in msg or "window" in msg):
        return "context_exceeded"
    if "timeout" in msg:
        return "timeout"
    return "internal"
