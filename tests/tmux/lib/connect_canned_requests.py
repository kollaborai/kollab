#!/usr/bin/env python3
"""Run Kollab (--no-daemon) with canned join requests, for the Connect screen specs.

A pending join request needs a second device and a relay, which a tmux spec does
not have. Every request row the Connect screen shows comes from
`RelayCommands._pending_rows`, and a decision goes through
`RelayAgentBridge.decide_enrollment_request`; this stands in for those two, so the
real screen, keys and renderer run against requests whose device names the spec
picks. Test tooling only: nothing in the app imports it.

usage: connect_canned_requests.py <device-name> [<device-name> ...]
"""

import sys
from types import SimpleNamespace

from kollabor_cli_main import cli_main
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_commands import RelayCommands

rows = [
    SimpleNamespace(
        enrollment_id=f"{number:032x}",
        device_name=name,
        device_key_fingerprint="abcd" + "0" * 56 + "ef01",
        credential_categories=(),
    )
    for number, name in enumerate(sys.argv[1:], start=1)
]


async def decide(self, enrollment_id, *, decision, source_agent):
    rows[:] = [row for row in rows if row.enrollment_id != enrollment_id]
    return {"status": decision}


RelayCommands._pending_rows = lambda self: list(rows)
RelayAgentBridge.decide_enrollment_request = decide
sys.argv = ["kollab", "--no-daemon"]
cli_main()
