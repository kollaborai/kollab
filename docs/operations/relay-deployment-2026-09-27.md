# Relay deployment record — 2026-09-27 UTC

Status: dated acceptance summary for a current development build. This evidence predates the Kollab 0.9.0 package release and came from a source artifact and does not prove behavior of a published PyPI installation or current service liveness. Private host paths, host aliases, infrastructure topology, raw logs, and raw evidence files are intentionally omitted from this public copy.

## Verified behavior

- At the recorded check, the public health endpoint returned HTTP 200 and signed discovery advertised the relay while it was healthy.
- Two independent client hosts connected through public WSS. Before approval, a ping was rejected. After explicit local approval, both directions completed authenticated encrypted ping/pong. Clients retained identity and origin pins across the tested recovery and reconnected with new transport sessions.
- A worker-process failure recovered public readiness in 9.34 seconds. A separate backend outage produced a visible HTTP 503 interval; readiness recovered in 31.41 seconds and clients reconnected. This was not lossless failover or uninterrupted availability.
- Public `/relay/v1/metrics` returned HTTP 404. Those relay-deployment checks did not authorize model turns, A2A tasks, remote file or shell actions, or a general agent conversation; they are not evidence about the later source-based conversation run recorded below.

## Capacity observations

These short runs describe only the tested source build and workload; they are not an SLA or a maximum-capacity result.

- At 1,024 low-rate connections: 1,535 of 1,535 encrypted exchanges completed, no client errors, 50.9 exchanges/second during the emission window, and 507 ms p95 latency.
- At 512 connections across four generators: 16,237 of 16,237 exchanges completed, no client errors, and 457.65 exchanges/second over the measured interval. Per-generator p95 latency was 691–727 ms.
- At 1,024 higher-rate connections across eight generators: 11,230 exchanges completed and 31 generic client errors were recorded, at 276.56 exchanges/second and about 4.2 seconds p95. The recorded data did not identify the cause of those client errors; it does not prove server saturation.
- No million-connection result, long-duration availability guarantee, lossless offline queue, or host-loss recovery claim was established.

A separate Redis Cluster recovery check observed worker rebind and fresh encrypted responses after shard movement and primary failover. It does not establish Valkey Cluster interoperability. The relay's native encrypted message kinds in these deployment checks were `ping` and `pong`. Later unreleased Kollab source tunneled mutual-TLS conversation records inside the relay's existing opaque Box transport; the relay still did not parse or execute A2A tasks or general agent text.

The detailed private deployment ledger supplied these summarized observations. This public release copy links to no raw operational evidence and makes no claim that the deployed source artifact is identical to a published package.

## Follow-on source metrics and public proxy check — 2026-09-27 10:11 UTC

The current unreleased Kollab source exports private enrollment admission gauges
from each worker's `/relay/v1/metrics` handler:
`relay_enrollment_admission_metrics_available`,
`relay_enrollment_rate_buckets_tracked`,
`relay_enrollment_rate_buckets_limit`,
`relay_enrollment_nonce_records_tracked`, and
`relay_enrollment_nonce_records_limit`. Redis-backed tracked counts use `ZCARD`
on the shared `{mailbox}` rate-source and nonce indices; these are shared index
cardinalities, not per-worker subtotals to be summed. Expired members may remain
counted until bounded admission cleanup, so the gauges are not exact live-record
counts. A non-transactional pipeline samples the two indices. If the backend
read fails, availability is `0` and tracked counts are `-1`, while configured
limits remain present. The unauthenticated worker endpoint must remain private.

A direct `GET https://kollabor.ai/relay/v1/metrics` at 2026-09-27 10:11:58 UTC
returned HTTP 404 with `application/json`. This is current evidence that the
public proxy still hides the metrics route at that time. It does not prove the
private listener works or establish whether these source changes are deployed.
The earlier public 404 and the recorded 2026-09-27 capacity runs above remain
historical evidence for their original checks.

## Source-based agent conversation — 2026-09-27 22:10–22:14 UTC

A later live run, `c2af5e59e5c743259dec129747f5987e`, used an unreleased source
snapshot (manifest SHA-256 prefix `2942e10178a9`) on the Mac and alzan-prod.
The attached `/connect status` check passed on both machines. The core exchange
also passed: the sender's `gpt-5.6-luna` model used native `hub_msg`; the remote
`gpt-5.6-luna` model executed normal `file_create` and `file_read` tools in its
own test workspace; the resulting 56-byte file had SHA-256
`48fe71c8827abfe83932971e8f9484e5f146cfc28af30dabf005f6185659e037`; and the
correlated completed reply was consumed in the sender model's actual provider
request and response. This demonstrates one real cross-host model/tool/file/reply
flow through the public relay on development source. It is not direct peer
dialing, a published-package check, or proof of the full networking goal.

The same-task question/answer follow-up did not pass: initial sender dispatch
timed out with `sender_hub_tool_missing`, and no Q&A task reached the receiver.
The authorization guard correctly rejected calls whose kind or exact purpose
did not match the human grant. Sender/receiver guidance was corrected in a
separate isolated source snapshot without weakening those checks. This result
describes run `c2af5e59e5c743259dec129747f5987e`; a later guidance-candidate run
is recorded below. Follow-up completion, cancellation, reconnect recovery, and
negative authorization cases remain open. The detailed evidence and failure
trace are in the private [network release review](network-release-review.md).

## Second source-based conversation run

Run `093d156e8d7d4f0988cd44c929d7796c` used a separate isolated copy of the
guidance candidate (manifest SHA-256
`c39f5ba7d4b0c9c5d7386637b39acf28609105ee897d4412b323878e861d001b`). The
attached `/connect status` checks on both hosts and the actual-model file/tool/
reply flow passed again. Task `d3e63d22695918530c966f184f93820a` completed; its
56-byte remote artifact has SHA-256
`8d86a7603e25e9f0200219afbf8d207c88ff23911e8598b371b5e358e6da5008`, and
result `7b6055fdce1fc2e13926f83da1fb61a6` reached the sender's provider request
and successful response. The Q&A task was admitted and its question reached the
sender ledger, but the verifier stopped before submitting the human answer with
`question_event_correlation_invalid`. The verifier incorrectly required the
question event itself to be marked delivered; runtime records transport in the
outbound queue while keeping the question pending for answer admission. The
exact admitted task was canceled through the normal workspace-owner path during
cleanup. This run does not prove answer delivery or task resumption; full Q&A
remains unverified. The verifier and cleanup corrections are still in progress.
See the [network release review](network-release-review.md) for the exact task,
event, artifact hash and failure diagnosis.
