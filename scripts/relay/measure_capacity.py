"""Measure an explicitly chosen relay with generated, mutually approved clients.

This exercises public signed discovery, WSS, peer rosters and authenticated
encrypted ping/pong. It never attaches a real workspace or dispatches tools.
Use only on a relay you operate; keep connection counts within its resource
budget. Output is a measurement, not an extrapolated capacity guarantee.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import resource
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plugins.hub.dns.discovery import discover  # noqa: E402
from plugins.hub.relay_client import RelayClient, RelayError  # noqa: E402
from plugins.hub.relay_commands import RelayCommands  # noqa: E402

_RELAY_ERROR_CODES = {
    "peer requires explicit local approval": "approval_required",
    "peer is offline": "peer_offline",
    "pending ping capacity reached": "pending_capacity",
    # RelayClient.ping wraps both its send timeout and pong timeout with
    # this text. The measurement cannot distinguish those two boundaries.
    "no authenticated pong received before deadline": "send_or_pong_deadline",
    "peer is unapproved or offline": "unapproved_or_offline",
    "relay frame too large": "frame_size",
    "ciphertext size limit exceeded": "ciphertext_size",
    "relay send rate limit reached": "client_rate_limited",
    "relay is not connected": "relay_not_connected",
    "relay disconnected": "relay_disconnected",
    "peer went offline or changed session": "peer_session_changed",
    "peer approval revoked": "approval_revoked",
    "relay transport error: backend_unavailable": "transport_backend_unavailable",
    "relay transport error: peer_offline": "transport_peer_offline",
    "relay transport error: not_registered": "transport_not_registered",
    "relay transport error: rate_limited": "transport_rate_limited",
    "relay transport error: invalid_frame": "transport_invalid_frame",
}


def safe_error_code(exc: Exception) -> str:
    """Classify known client failures without emitting arbitrary exception text."""
    if isinstance(exc, RelayError):
        message = exc.args[0] if len(exc.args) == 1 and isinstance(exc.args[0], str) else ""
        return "RelayError:" + _RELAY_ERROR_CODES.get(message, "unclassified")
    return type(exc).__name__


def percentile(values: list[float], percent: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(values) * percent / 100) - 1)], 2)


async def measure(args) -> dict:
    usage_start = resource.getrusage(resource.RUSAGE_SELF)
    discovery = await discover(args.origin)
    ws_url = RelayCommands._relay_url(discovery)
    if not ws_url:
        raise ValueError("origin does not advertise a compatible relay")
    directory = Path(tempfile.mkdtemp(prefix="kollab-relay-capacity-"))
    report = {
        "origin": discovery.origin,
        "publisher_principal_id": discovery.publisher_principal_id,
        "discovery_evidence": "fresh HTTPS-origin signed descriptor; no persistent origin pin in this isolated probe",
        "protocol": "kollab-relay/1",
        "started_at": int(time.time()),
        "python": platform.python_version(),
        "generator_cpu_count": os.cpu_count(),
        "generator_fd_soft_limit": resource.getrlimit(resource.RLIMIT_NOFILE)[0],
        "requested_connections": args.connections,
        "pair_rate_per_second": args.rate,
        "requested_duration_seconds": args.duration,
        "drain_timeout_seconds": args.drain_timeout,
        "registration_timeout_seconds": args.setup_timeout,
        "load_model": "closed loop: at most one outstanding ping per pair; latency may reduce the offered rate",
        "rate_windows": (
            "offered and completed rates use the emission window; total completions also include bounded drain"
        ),
        "latency_population": "authenticated completions, including drain; censored requests excluded",
        "private_client_state": str(directory),
    }
    clients: list[RelayClient] = []
    pairs: list[tuple[RelayClient, RelayClient]] = []
    connection_ms: list[float] = []
    connection_attempt_ms: list[float] = []
    latency_ms: list[float] = []
    errors: Counter = Counter()
    counts: Counter = Counter()
    semaphore = asyncio.Semaphore(args.concurrency)

    async def connect(client):
        start = time.monotonic()
        counts["registration_attempts"] += 1
        try:
            state = await client.connect(discovery.origin, ws_url=ws_url)
        finally:
            connection_attempt_ms.append((time.monotonic() - start) * 1000)
        if state["state"] != "online":
            errors["registration:" + (state["error"] or state["state"])] += 1
            await client.close(disable=True)
            return False
        counts["registered_connections"] += 1
        connection_ms.append((time.monotonic() - start) * 1000)
        return True

    async def open_pair(index):
        async with semaphore:
            left = RelayClient(directory / f"workspace-{index}-a", state_dir=directory / f"client-{index}-a")
            right = RelayClient(directory / f"workspace-{index}-b", state_dir=directory / f"client-{index}-b")
            clients.extend((left, right))
            ready = False
            try:
                if not await connect(left):
                    return
                right.join_invite(left.invite())
                left.approve(right.public_key)
                if not await connect(right):
                    return
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    if any(p["key"] == right.public_key for p in left.peers()) and any(
                        p["key"] == left.public_key for p in right.peers()
                    ):
                        pairs.append((left, right))
                        ready = True
                        return
                    await asyncio.sleep(0.05)
                errors["pair_presence_deadline"] += 1
            except Exception as exc:
                errors["setup:" + safe_error_code(exc)] += 1
            finally:
                if not ready:
                    # A half-open pair must not occupy capacity needed by
                    # subsequent registrations or inflate admitted counts.
                    await asyncio.gather(left.close(disable=True), right.close(disable=True), return_exceptions=True)

    async def exercise(left, right, deadline):
        period = 1 / args.rate
        # Offset pair loops so this measures sustained work, not a lockstep
        # burst caused by the load generator's own scheduling.
        offset = (int(left.public_key[:4], 16) / 65536) * period
        remaining = max(0, deadline - time.monotonic())
        counts["scheduled_ping_slots"] += max(0, math.ceil((remaining - offset) / period))
        await asyncio.sleep(min(offset, remaining))
        while True:
            start = time.monotonic()
            if start >= deadline:
                break
            counts["attempted_pings"] += 1
            observation = asyncio.timeout_at(deadline + args.drain_timeout)
            try:
                # Stop issuing at deadline, but observe already-issued work
                # through a separate bounded drain. A collector deadline is
                # censoring, not evidence that the relay failed the ping.
                async with observation:
                    response = await left.ping(right.public_key)
                if response.get("workspace_id") != right.state.workspace_id:
                    errors["wrong_authenticated_workspace"] += 1
                    counts["failed_pings"] += 1
                else:
                    completed = time.monotonic()
                    latency_ms.append((completed - start) * 1000)
                    counts["pongs_within_window" if completed <= deadline else "pongs_during_drain"] += 1
            except TimeoutError:
                if observation.expired():
                    counts["censored_pings"] += 1
                else:
                    errors["ping:TimeoutError"] += 1
                    counts["failed_pings"] += 1
            except Exception as exc:
                errors["ping:" + safe_error_code(exc)] += 1
                counts["failed_pings"] += 1
            await asyncio.sleep(min(max(0, deadline - time.monotonic()), max(0, period - (time.monotonic() - start))))

    try:
        start = time.monotonic()
        try:
            async with asyncio.timeout(args.setup_timeout):
                await asyncio.gather(*(open_pair(index) for index in range(args.connections // 2)))
        except TimeoutError:
            errors["registration_global_deadline"] += 1
        report["registration_seconds"] = round(time.monotonic() - start, 3)
        report["admitted_connections"] = sum(client.status()["state"] == "online" for client in clients)
        report["ready_pairs"] = len(pairs)
        report["registration_p95_ms"] = percentile(connection_ms, 95)
        report["registration_attempt_p95_ms"] = percentile(connection_attempt_ms, 95)
        start = time.monotonic()
        report["load_started_at"] = time.time()
        deadline = start + args.duration
        await asyncio.gather(*(exercise(left, right, deadline) for left, right in pairs))
        duration = time.monotonic() - start
        report["load_finished_at"] = time.time()
        emission_duration = min(duration, args.duration)
        report.update(
            observed_seconds=round(duration, 3),
            emission_window_seconds=round(emission_duration, 3),
            drain_seconds=round(max(0, duration - args.duration), 3),
            authenticated_pongs=len(latency_ms),
            authenticated_pongs_within_window=counts["pongs_within_window"],
            authenticated_pongs_during_drain=counts["pongs_during_drain"],
            attempted_pings=counts["attempted_pings"],
            failed_pings=counts["failed_pings"],
            censored_pings=counts["censored_pings"],
            scheduled_ping_slots=counts["scheduled_ping_slots"],
            missed_ping_slots=max(0, counts["scheduled_ping_slots"] - counts["attempted_pings"]),
            registration_attempts=counts["registration_attempts"],
            registered_connections=counts["registered_connections"],
            offered_pings_per_second=round(counts["attempted_pings"] / max(emission_duration, 0.001), 2),
            completed_pings_per_second=round(counts["pongs_within_window"] / max(emission_duration, 0.001), 2),
            completed_pings_per_second_including_drain=round(len(latency_ms) / max(duration, 0.001), 2),
            ping_p50_ms=percentile(latency_ms, 50),
            ping_p95_ms=percentile(latency_ms, 95),
            ping_p99_ms=percentile(latency_ms, 99),
            online_at_end=sum(client.status()["state"] == "online" for client in clients),
            errors=dict(errors),
            client_counters=dict(sum((Counter(client.status()["counters"]) for client in clients), Counter())),
        )
    finally:
        cleanup = await asyncio.gather(*(client.close(disable=True) for client in clients), return_exceptions=True)
        report["cleanup_errors"] = sum(isinstance(result, BaseException) for result in cleanup)
        report["connected_after_cleanup"] = sum(client.status()["state"] != "disconnected" for client in clients)
        usage_end = resource.getrusage(resource.RUSAGE_SELF)
        report["generator_cpu_seconds"] = round(
            usage_end.ru_utime + usage_end.ru_stime - usage_start.ru_utime - usage_start.ru_stime, 3
        )
        report["generator_peak_rss_bytes"] = usage_end.ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--connections", type=int, default=8, help="even number, maximum 2048")
    parser.add_argument("--concurrency", type=int, default=8, help="parallel pair registrations, maximum 64")
    parser.add_argument("--rate", type=float, default=1, help="pings per pair per second, 0.01..4")
    parser.add_argument("--duration", type=float, default=20, help="seconds, maximum 300")
    parser.add_argument(
        "--drain-timeout", type=float, default=12, help="observe in-flight pings after emission stops, 0..30 seconds"
    )
    parser.add_argument(
        "--setup-timeout", type=float, default=120, help="whole registration phase deadline, 1..600 seconds"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.connections <= 2048 or args.connections % 2:
        parser.error("connections must be an even number in 2..2048")
    if (
        not 1 <= args.concurrency <= 64
        or not 0.01 <= args.rate <= 4
        or not 1 <= args.duration <= 300
        or not 0 <= args.drain_timeout <= 30
        or not 1 <= args.setup_timeout <= 600
    ):
        parser.error("concurrency, rate, duration or deadline is outside its bounded range")
    if args.output.exists():
        parser.error("output already exists; choose a new evidence path")
    report = asyncio.run(measure(args))
    with args.output.open("x") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
