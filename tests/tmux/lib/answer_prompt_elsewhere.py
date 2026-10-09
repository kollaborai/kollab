"""Answer the open permission prompt of this HOME's only live agent from another window.

The web UI attaches to a terminal's agent the same way (kollabor_engine's
DaemonPool.adopt): this attaches, finds the prompt in the replayed events,
answers it over RPC, and detaches. The agent's own terminal must then close its
copy of the prompt (specs/attach_prompt_answered_elsewhere.json).

usage: HOME=<spec home> answer_prompt_elsewhere.py [response]
"""

import asyncio
import sys

from kollabor_engine.daemon_pool import DaemonPool
from kollabor_engine.hub_bridge import HubBridge


async def main(response: str) -> None:
    rows = HubBridge().discover_sessions(use_cache=False)
    if len(rows) != 1:
        sys.exit(f"expected one live agent in this HOME, found {len(rows)}")
    pool = DaemonPool()
    handle = await pool.adopt(rows[0]["session_id"], rows[0])
    events = handle.subscribe()  # before the first await: the replay is next
    try:
        while True:
            event = await asyncio.wait_for(events.get(), timeout=10)
            if event.get("type") == "permission_request":
                tool_id = event["tool_id"]
                break
        reply = await handle.rpc.call("permission.respond", {"tool_id": tool_id, "response": response})
        print(f"answered {tool_id}: {reply}")
    finally:
        await pool.stop(rows[0]["session_id"])  # detaches; the agent keeps running


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "APPROVE_ONCE"))
