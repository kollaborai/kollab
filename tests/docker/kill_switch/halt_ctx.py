"""halt_ctx.py snap | members | act <action> [json-payload]: the halt panels' calls
for this folder, through the halt listener when one runs (halted machine), else
the folder's live agent. Run from the folder (cwd). Test harness only."""

import asyncio
import json
import os
import sys

from plugins.hub.halt_context import HaltContext


async def main() -> None:
    ctx = HaltContext(os.getcwd())
    op = sys.argv[1]
    if op == "snap":
        out = await ctx.hub_halt_snapshot()
    elif op == "members":
        out = await ctx.hub_halt_members()
    else:
        payload = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        out = await ctx.hub_halt_act(sys.argv[2], {"confirmed": True, **payload})
    print(json.dumps(out))


try:
    asyncio.run(main())
except Exception as exc:  # RelayError/HaltError text is operator-safe
    print(json.dumps({"error": str(exc)}))
