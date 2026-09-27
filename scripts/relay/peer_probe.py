"""Drive the real /connect controller over stdin on an operator-owned host.

Each input line is one command (for example ``status`` or ``ping <public key>``).
Invitation contents are read from private files, never printed. No model or
workspace tool is started. Exit with EOF or ``quit``.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plugins.hub.relay_commands import RelayCommands  # noqa: E402


async def run(args):
    controller = RelayCommands(args.workspace, state_dir=args.state_dir)
    try:
        first = "join " + str(args.invitation_file) if args.invitation_file else args.origin
        print(json.dumps({"result": await controller.run(first), "status": controller.client.status()}), flush=True)
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line or line.strip() == "quit":
                break
            command = line.strip()
            print(
                json.dumps({"result": await controller.run(command), "status": controller.client.status()}),
                flush=True,
            )
    finally:
        await controller.client.close(disable=True)
        await controller.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://kollabor.ai")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--invitation-file", type=Path)
    asyncio.run(run(parser.parse_args()))
