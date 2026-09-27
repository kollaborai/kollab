# Relay deployment record — 2026-09-27 UTC

Status: dated acceptance summary for a current development build. This evidence predates the Kollab 0.9.0 package release and came from a source artifact and does not prove behavior of a published PyPI installation or current service liveness. Private host paths, host aliases, infrastructure topology, raw logs, and raw evidence files are intentionally omitted from this public copy.

## Verified behavior

- At the recorded check, the public health endpoint returned HTTP 200 and signed discovery advertised the relay while it was healthy.
- Two independent client hosts connected through public WSS. Before approval, a ping was rejected. After explicit local approval, both directions completed authenticated encrypted ping/pong. Clients retained identity and origin pins across the tested recovery and reconnected with new transport sessions.
- A worker-process failure recovered public readiness in 9.34 seconds. A separate backend outage produced a visible HTTP 503 interval; readiness recovered in 31.41 seconds and clients reconnected. This was not lossless failover or uninterrupted availability.
- Public `/relay/v1/metrics` returned HTTP 404. The checks did not authorize model turns, A2A tasks, remote file or shell actions, or a general agent conversation.

## Capacity observations

These short runs describe only the tested source build and workload; they are not an SLA or a maximum-capacity result.

- At 1,024 low-rate connections: 1,535 of 1,535 encrypted exchanges completed, no client errors, 50.9 exchanges/second during the emission window, and 507 ms p95 latency.
- At 512 connections across four generators: 16,237 of 16,237 exchanges completed, no client errors, and 457.65 exchanges/second over the measured interval. Per-generator p95 latency was 691–727 ms.
- At 1,024 higher-rate connections across eight generators: 11,230 exchanges completed and 31 generic client errors were recorded, at 276.56 exchanges/second and about 4.2 seconds p95. The recorded data did not identify the cause of those client errors; it does not prove server saturation.
- No million-connection result, long-duration availability guarantee, lossless offline queue, or host-loss recovery claim was established.

A separate Redis Cluster recovery check observed worker rebind and fresh encrypted responses after shard movement and primary failover. It does not establish Valkey Cluster interoperability. The relay's only supported native encrypted message kinds are `ping` and `pong`; the relay does not carry A2A tasks or general agent text.

The detailed private deployment ledger supplied these summarized observations. This public release copy links to no raw operational evidence and makes no claim that the deployed source artifact is identical to a published package.
