"""Diagnose an operator-owned relay through Kollab's existing RelayClient.

Use an isolated --state-dir outside this machine's managed network state.
Stdin commands: status, peers, approve KEY, revoke KEY, ping KEY, invite FILE,
join FILE, leave. KEY is a full public key. invite FILE creates a new mode-0600
file; join FILE reads an existing private file. Invitations are never printed.
Signed discovery and durable publisher pins select the relay. Peer approval
permits transport diagnostics; no application handler, model or tool is started.
Exit with EOF or quit; the client is disconnected and disabled on exit.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plugins.hub.dns.discovery import discover, relay_websocket_url  # noqa: E402
from plugins.hub.dns.discovery_store import DiscoveryStore  # noqa: E402
from plugins.hub.relay_client import RelayClient  # noqa: E402
from plugins.hub.relay_state import (  # noqa: E402
    RelayError,
    RelayStateStore,
    is_machine_network_state_dir,
    parse_invite,
)


async def attach(client: RelayClient, origin: str, invitation_file: Path | None = None):
    token = None
    if invitation_file is not None:
        token = RelayStateStore._read_private(invitation_file, 4096).strip()
        origin = parse_invite(token)["origin"]
    result = await discover(origin)
    result = await asyncio.to_thread(
        DiscoveryStore(client.state_dir / "discovered").accept, result
    )
    ws_url = relay_websocket_url(result)
    if ws_url is None:
        raise ValueError("origin does not advertise a compatible relay")
    if token is not None:
        await client.close()
        client.join_invite(token)
    return await client.connect(result.origin, ws_url=ws_url)


async def command(client: RelayClient, value: str):
    if len(value) > 4096:
        raise ValueError("command is too large")
    head, _, rest = value.strip().partition(" ")
    rest = rest.strip()
    if head == "status" and not rest:
        return client.status()
    if head == "peers" and not rest:
        return client.peers()
    if head == "approve" and rest:
        client.approve(rest)
        return "approved"
    if head == "revoke" and rest:
        client.revoke(rest, announce=True)
        return "revoked"
    if head == "ping" and rest:
        return await client.ping(rest)
    if head == "invite" and rest:
        token = client.invite()
        descriptor = os.open(rest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(token + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return "invitation saved"
    if head == "join" and rest:
        return await attach(client, "", Path(rest))
    if head == "leave" and not rest:
        await client.leave()
        return "left"
    raise ValueError("unsupported diagnostic command; see --help")


def error_result(exc: Exception) -> dict:
    # Exception text can contain a private path or untrusted discovery data.
    code = getattr(exc, "code", None)
    if code not in {"managed_state", "state_ownership"}:
        code = type(exc).__name__
    return {"error": code}


async def run(args):
    if is_machine_network_state_dir(args.state_dir):
        raise RelayError("probe requires isolated state", "managed_state")
    client = RelayClient(args.workspace, state_dir=args.state_dir)
    try:
        result = await attach(client, args.origin, args.invitation_file)
        print(json.dumps({"result": result, "status": client.status()}), flush=True)
        while True:
            line = await asyncio.to_thread(sys.stdin.readline, 4098)
            if not line or line.strip() == "quit":
                break
            try:
                if len(line) > 4096:
                    # Drain one oversized input line without interpreting its tail.
                    while line and not line.endswith("\n"):
                        line = await asyncio.to_thread(sys.stdin.readline, 4098)
                    raise ValueError("command is too large")
                result = await command(client, line.strip())
            except (OSError, ValueError, TimeoutError) as exc:
                result = error_result(exc)
            print(json.dumps({"result": result, "status": client.status()}), flush=True)
    finally:
        await client.close(disable=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://kollabor.ai")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--invitation-file", type=Path)
    try:
        asyncio.run(run(parser.parse_args()))
    except Exception as exc:
        print(json.dumps(error_result(exc)), file=sys.stderr)
        raise SystemExit(1) from None
