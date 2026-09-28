# Agent networking goal: work breakdown and progress

Updated: 2026-09-28

This is the execution tracker for the complete networking goal in
`/Users/malmazan/.codex/attachments/1ab030ab-9bc6-415e-9822-e3c55a121d3c/goal-objective.md`.
It records evidence boundaries as well as implementation status. “Partial” and
“in progress” are not completion claims.

Current status (2026-09-28): released as Kollab 0.10.0 and 0.10.1. Clean-install
acceptance passed on the public beacon and on a second, self-hosted origin.
Enrollment works from any trusted agent. Open: peer-mesh links beyond one relay
room (LAN/direct bootstrap, relay-less operation, cross-room forwarding).

ETA for the open mesh work: about two days including live proofs.

## Completion target

Deliver Kollab networking end to end: public and self-hosted discovery; private
identity, pairing, authorization and revocation; real model-to-model
conversations and remote workspace tools; local/direct/forwarded routing and
recovery; measured production limits; synchronized operator documentation; a
published release; and clean installed-package acceptance on the Mac and
`alzan-prod`.

## Work breakdown

| ID | Workstream | Current status | Exit evidence |
| --- | --- | --- | --- |
| 1 | Preserve and baseline the shared candidate | Done: all Codex-session work committed on `codex/relay-agent-messaging`, backups kept under `backup/*` refs; draft PR #87 open with CI green  | Current branch, dirty state and protected ownership recorded; no unrelated work overwritten. |
| 2 | Close source implementation blockers | In progress | Focused source changes and regressions for each listed blocker; no security boundary weakened. |
| 2.1 | Issuer enrollment recovery | Live on installed 0.10.1 (2026-09-28): device and issuer recover across a device restart. A relay restart ends in-flight enrollments because relay state is not durable; the issuer now says so (#94) | Exact approved request resumes only after rechecking owner, relay, origin, room, issuer workspace, recipient key, scope and expiry; it retries the same decision ciphertext. Expired/revoked incomplete rows revoke only the exact owner-signed credential after durable request/delegation checks. Completed peers are never revoked; receipted-but-unresolved and mismatched rows remain with fixed-code backoff. Confirm against a real relay process restart. |
| 2.2 | Destination enrollment completion recovery | Live on installed 0.10.1 (2026-09-28): the new device was killed while its request was pending, restarted, and finished enrollment from its journal after the issuer accepted | Persist the stable request before its first POST, then recover the same round through challenge, decision and ACK. Replay the exact signed ACK after a lost response/restart; finish credential import, invite join and attachment idempotently. Retry delay grows from 5 seconds to a 300-second cap; `/connect status` exposes only aggregate state and fixed error codes. |
| 2.3 | Agent conversation and authorization | Live-verified 2026-09-28 (run `0428cb8f`): human-granted send, receiver model and normal tools, correlated result, question/answer, follow-up, cancellation, reconnect; unauthorized, revoked, replay and wrong-workspace rejected at the receiver guard  | Normal Hub/model/tool path, correlated replies, Q&A, progress, errors, cancellation and deadlines work; verify duplicate, impersonation, replay, wrong-workspace, revoked and loop rejection. |
| 2.4 | Peer route integration | Partial. Live: relay-carried secure sessions between peers in one relay room. Not built: links with peers reachable only over LAN/direct, relay-less operation, and cross-room forwarding (the mesh only exchanges links with relay-roster peers) | Same-host discovery, direct TLS connections, relay fallback and signed bounded forwarding are wired; live tests must still prove cross-host routing and that intermediaries cannot decrypt or invoke ordinary Hub hooks. |
| 2.5 | Discovery, presence and reconnect reliability | Partial. Live: public and self-hosted discovery (kollabor.ai and selfhost.kollabor.ai), quiet same-user roster across workspaces, reconnect. Open: LAN household discovery without a relay | Public/self-hosted DNS contract and quiet local discovery; authenticated expiring records, bounded propagation, truthful offline/revoked status, route changes and restart recovery. |
| 2.6 | Shared-state and capacity limits | Measured 2026-09-28 on the production topology (2 workers + managed Valkey, same host), see Capacity below; prior 1,024-connection errors classified | Classify prior load errors; measure the final topology, recovery and resource/backpressure limits; publish only demonstrated operating limits. |
| 3 | Focused regressions and candidate verification | Full unit suite green on the branch; CI (security-scan, standards-check, tests) green on PR #87  | Run focused and cross-cutting suites for touched behavior; retain exact commands and results. Unit and simulated transport evidence remain separate from live proof. |
| 4 | Source-level two-host acceptance | Passed 2026-09-28: run `0428cb8f` through `https://kollabor.ai`, all eleven checks, current source on the Mac and `alzan-prod`  | Current source on Mac and `alzan-prod`: actual models, remote file operation, artifact/hash, correlated reply consumed by sender, follow-up answer, cancellation/reconnect and negative authorization cases. |
| 5 | Synchronize canonical docs and operator guidance | Seven walkthroughs exact and code-checked (PR #96); ledger and tracker updated with installed, self-hosted and capacity evidence | Specs, implementation ledger, walkthroughs, command help, harness and development skill match the implementation and verified limits. |
| 6 | Prepare and publish a corrected release | Done: Kollab 0.10.0 and 0.10.1 published to PyPI and GitHub Releases (tags `v0.10.0`, `v0.10.1`) | Version all packages consistently, changelog and CI pass, normal tag/release/PyPI workflow completes. |
| 7 | Installed-package acceptance | Done: clean `pip install kollab==0.10.1` on both hosts; runs `c947b361` (kollabor.ai) and `dbcd34d6` (selfhost.kollabor.ai) passed every check; enrollment from a fresh non-operator issuer passed | Clean `pip install kollab` environments on Mac and `alzan-prod`; repeat cross-network model/tool/reply flow and required recovery/negative cases. |
| 8 | Final completion report | Open | Release identifiers, exact working commands, source and installed evidence, and measured limits; all unverified items named. |

## Live acceptance on current source (2026-09-28)

Runs `0428cb8fb09a47ea821fe673e9930093` and `b42ec1abb234489b9c0be37d3a762fac`
both passed every check of
`scripts/relay/verify_agent_conversation.py` between the Mac and `alzan-prod`
through `https://kollabor.ai`: pairing, attached `/connect status` on both
hosts, the core model/tool/file exchange, question/answer, follow-up,
cancellation, reconnect, and receiver-guard rejection of unauthorized,
revoked, replayed and wrong-workspace requests. The manifest is private under
`~/.kollab/acceptance-evidence/` on the Mac.

Getting there took 23 runs. Runtime defects found and fixed on the way:

- The remote agent directory dropped relay peers once the peer mesh was active.
- After pairing, secure packets were routed through `peer.forward`, which fails
  live; relay-connected peers keep the native relay path unless direct dialing
  is enabled.
- Periodic mesh refresh re-signed unchanged peer links with new timestamps,
  which peers rejected as equivocation, tearing down healthy sessions.
- A relay-event turn could send a model-written answer to a remote question;
  answers now need a human turn.
- A task waiting on its question accepted a result and completed early.
- Cancelling left the task's queued result deliverable.
- Replies and answers required models to retype long IDs exactly; the runtime
  now binds them to the active task or pending question record.

The relay behind `kollabor.ai` runs release `20260928-789c657`, which adds the
enrollment mailbox routes (`/relay/v1/enrollment/`, POST only).

## Clean-install acceptance on 0.10.0 (2026-09-28)

Kollab 0.10.0 was published to PyPI from tag `v0.10.0` (`d490b61`). Both hosts
installed it into fresh virtual environments with `pip install kollab==0.10.0`,
and the pilots were relaunched from those installs (daemon command lines point
at the new venvs; both report `kollab 0.10.0`). Run
`381f49e978d64a7db2911126057fd4bb` passed every check: pairing, the core
model/tool/file exchange, ui-command, follow-up, question/answer, cancel,
reconnect, receiver-guard rejection of unauthorized, revoked and
wrong-workspace requests, and replay deduplication.

## Live code enrollment on source (2026-09-28, #89)

0.10.0 let only the discovery domain's coordinator issue codes, so the live run
used the #89 source instead. A brand-new issuer on the Mac (fresh home, one
profile with a fake API key, no real credentials anywhere) ran
`/connect kollabor.ai` and `/connect offer`; a brand-new device on `alzan-prod`
received the code by bracketed paste into bare `/connect`. The issuer listed
the redacted request (device fingerprint, workspace, profile and credential
category) and accepted it. Result: the device reported `beacon: online` with one
approved peer, the issuer listed the device key as approved, and the device's
provisioned state held the issuer's profile under network IDs naming the
issuer's own key rather than the kollabor.ai publisher.

The first attempt pasted the code into the domain field, which rendered it in
clear text; the entry view now moves anything from `K1-` onward into the masked
code field, and the second attempt exercised that path.

## Installed 0.10.1: acceptance, enrollment and restarts (2026-09-28)

- Both hosts reinstalled from PyPI (`pip install kollab==0.10.1`, fresh venvs);
  run `c947b361acf84fe09c2eeb58b2f6c329` passed every conversation check.
- Enrollment from the installed package: a brand-new Mac issuer holding only a
  fake API key issued a code, a brand-new device on `alzan-prod` enrolled, the
  issuer approved it, and the sealed profile landed under the issuer's own
  network ID.
- Device restart while its request was pending: the device process was killed
  and restarted, the issuer accepted, and the device finished enrollment from
  its recovery journal.
- Relay restart (`systemctl restart kollab-relay.service`, healthy again in
  about 5 s): the relay's managed Valkey keeps no durable state, so in-flight
  enrollments end. With the restart before the decision, `/connect accept`
  was refused (it now explains why, #94); with the restart one second after
  accept, the issuer showed accepted but the device never received the bundle
  and both sides retried until expiry. A new code is required after a relay
  restart.

## Self-hosted origin (2026-09-28)

A second deployment, `https://selfhost.kollabor.ai`, ran entirely from
`pip install kollab==0.10.1`: `kollab relay run` (2 workers + managed Valkey),
the discovery publisher with its own signing key, a static well-known server,
and a TLS proxy with the same routes as kollabor.ai (DNS A record, `_agent` TXT
locator, Let's Encrypt certificate). From both hosts, signed discovery verified
the new origin; code enrollment from a fresh fake-key issuer completed under
its own origin ID; and run `dbcd34d61ae04ded8a73268bcb771268` passed every
conversation check (fresh workspaces paired by the harness, real model and
tool execution, the created file verified on the remote host).

## Quiet local roster (2026-09-28)

On the Linux host, `/connect agents local` listed agents from three workspace
folders under one OS user, with coordinator role, workspace, process and online
state. It is a local RPC: no model turn, broadcast or publication. Isolation
from other OS users was not tested live.

## Proxy fixes on kollabor.ai (2026-09-28)

- `POST /relay/v1/contact/*` was not proxied (404), so unknown-contact requests
  could not reach the relay. Added, POST only, on both origins.
- The relay answers over-quota registrations with 503. The proxy listed
  `http_503` in `proxy_next_upstream` with `max_fails=2`, so two such answers
  marked both workers unavailable for 5 s and every other client failed too:
  one client over its per-source quota could black out the relay. `http_503`
  is no longer treated as an upstream failure; over-quota runs afterwards
  produced relay 503s and no new "no live upstreams" events.

## Capacity (2026-09-28)

Measured with `scripts/relay/measure_capacity.py` against the self-hosted
origin: same host and topology as production (2 relay workers, managed Valkey),
per-source limit raised to 1,024 for the test only, because both generator
hosts share one public IP. Closed-loop encrypted ping/pong; generators on the
Mac and the Linux host.

| Concurrent connections | Completed pings/s | p50 / p99 | Failed pings |
| --- | --- | --- | --- |
| 250 (one generator, Linux host) | ~248 | 141 / 273 ms | 0 of 11,211 |
| 500 (one generator, Mac) | ~209 | 1.06 / 1.37 s (generator-bound) | 0 of 9,653 |
| 1,000 (two generators, 500 each) | ~575 | 0.35–1.0 s / 2.6–3.2 s | 32 of 26,350 |
| 1,000 (four generators) | ~595 | ~0.55 s / 3.2–4.4 s | 143 of 27,303 |

At 1,000 connections one worker used about half a core, 84 MB peak RSS and
~550 file descriptors; Valkey stayed near 11 MiB. Failures were ping deadlines,
brief `peer_offline`/`relay_disconnected` drops and session changes, with every
connection online again at the end: queueing in the two worker event loops,
not crashes. The 31 unclassified errors from the 2026-09-27 1,024-connection run
match this pattern. Production limits remain 16 connections per source, 16 per
room and 512 per worker. No soak, multi-host backend or larger-worker-count
result is claimed.

## Relay runtime supervision (2026-09-28, #97)

A supervisor killed without a clean stop left its workers running (own
session) and renewing owner leases, so a new `kollab relay run` gave up after
45 s. Workers now stop themselves when their supervisor PID disappears, and
startup waits out a previous owner's lease. Live: after SIGKILL of the
supervisor both workers exited and a restart was ready in 5 s.

## Evidence already recorded

- The 0.9.0 deployment proved encrypted relay ping/pong, not an agent
  conversation.
- Two unreleased source-pilot runs recorded an actual Mac-to-`alzan-prod`
  model/tool/file exchange and a correlated result reaching the sender model.
  The second artifact was 56 bytes with SHA-256
  `8d86a7603e25e9f0200219afbf8d207c88ff23911e8598b371b5e358e6da5008`.
- The second run delivered a follow-up question to the sender ledger, but the
  verifier stopped before the human answer because it checked the wrong
  delivery field. Full Q&A remains unverified. Cancellation and the required
  negative/reconnect cases remain open.
- Since the earlier peer review, `RelayAgentBridge` now creates, starts,
  refreshes and closes `PeerMeshRuntime`, and the authenticated endpoint listener
  dispatches only the opaque `peer_forward` carrier to it. Root reran the
  verifier, bridge, peer transport, locator and discovery suites together:
  202 passed. The suite includes loopback TLS, candidate-only UDP discovery,
  and in-process encrypted A-to-C-to-B forwarding with replay rejection. These
  checks do not establish a live cross-host, LAN, relay-mesh or multihop route.
- The release audit found PyPI at 0.9.0 at its audit point. No corrected
  package publication or clean-install acceptance is established here.
- Prior capacity notes include successful 512-client and low-rate 1,024-client
  runs, plus 31 timeouts in a heavier 1,024-client run. Treat those as prior
  candidate evidence; rerun and classify against the final topology before
  publishing an operating envelope.

## Previous source slice: 2.1

Worktree: `/Users/malmazan/.codex/worktrees/kollab-release/kollab`, branch
`codex/relay-agent-messaging`, base `ee60e38`. The checkout already contains
extensive shared dirty work. Preserve it; do not reset, stash, switch branches,
commit broadly, push or publish as part of this slice. The shared owner-reserved
`plugins/hub/messenger.py` and `plugins/hub/peer_discovery.py` surfaces are not
being edited here.

Changes made and verified in this update:

- Added `plugins/hub/enrollment_recovery.py`: bounded SecretBox-encrypted local
  journal keyed from the stable RelayClient signing key, atomic writes, and
  private directory/file checks.
- Made `PrivateDirectory.approve_pairing()` return the same existing credential
  for the same approved challenge, proof, scopes and lifetime; a different
  proof or terms are rejected.
- Integrated issuer recovery in `plugins/hub/enrollment_client.py` and startup
  scheduling in `plugins/hub/relay_agent.py`. The encrypted record captures the
  exact approved request, provisioning material, issued credential and exact
  decision ciphertext. Restore requires the durable approval ledger, current
  online relay, fresh discovery, and matching issuer/room/workspace/recipient/
  scope/expiry. The relay only approves the destination after a signed install
  receipt matches the expected digest.
- Fixed two defects found by the issuer acceptance test: recovery records are
  indexed by offer ID (not enrollment ID), and minimal discovery objects can
  derive the recovery domain from their authority or origin.
- Added restart/replay regression coverage. It simulates a new issuer and
  RelayClient session against persisted state and a fake HTTP transport, checks
  byte-for-byte reuse of the encrypted decision, and confirms mismatched scope,
  revoked approval and expired offers do not resume.
- Updated failed-ACK expectations: the approved credential and exact envelope
  remain available for bounded retry, while no install receipt or relay
  admission is recorded until a valid receipt arrives.
- Updated the local Hub command test fixture to use a real `_ProvisioningPlan`
  and `ProfilePreferences`, matching the recovery journal’s production schema.

Validation evidence:

- `.venv/bin/python -m pytest -q tests/unit/test_enrollment_recovery.py tests/unit/test_private_directory.py tests/unit/test_enrollment_client.py`
  → `48 passed in 1.41s`.
- `.venv/bin/python -m py_compile` on the touched Python modules and tests, then
  `git diff --check` on the same paths → both passed (exit 0).
- Broader enrollment, RPC, relay bridge and peer-session sweep using the
  existing dependency-complete locked environment:
  `timeout 180s /tmp/kollab-network-locked-venv/bin/python -m pytest -q tests/unit/test_enrollment_recovery.py tests/unit/test_enrollment_client.py tests/unit/test_enrollment_codes.py tests/unit/test_enrollment_delegations.py tests/unit/test_private_directory.py tests/unit/test_relay_enrollment.py tests/unit/test_hub_connect_enrollment.py tests/unit/test_hub_enrollment_rpc.py tests/unit/test_hub_enrollment_commands.py tests/unit/test_relay_agent_bridge.py tests/unit/test_relay_client_peer_sessions.py`
  → `186 passed in 11.79s`.
- The default shell Python lacks pytest, and the repo `.venv` lacks
  `cryptography`; the pre-existing locked environment has the required test
  dependencies. No packages were installed.
- These are focused local tests with a fake HTTP transport. They do not prove
  a real separate-process restart, live relay recovery, or Mac↔`alzan-prod`
  acceptance.

## Current execution slice: 2.2

Implemented in `plugins/hub/enrollment_client.py` and
`plugins/hub/enrollment_recovery.py`:

- A separate domain-derived key protects a bounded destination journal in the
  RelayClient state directory. It stores the stable round, verifier, exact
  request and envelope key before the first POST; the plaintext code is never
  persisted.
- The owner-signed challenge now carries an expiry. The destination saves the
  challenge, exact proof and exact signed decision before advancing state, and
  rechecks origin, owner, issuer, destination, workspace, scope and expiry.
- Provisioning install, credential import and invite completion are replay
  safe. Conflicting saved invite state fails closed, and a matching invitation
  preserves additional local approvals.
- The local install receipt and exact encrypted ACK are saved before ACK POST.
  Lost-response recovery replays the same envelope/round/destination. The
  existing `post_signed_retry()` creates fresh outer signed frames. Startup
  recovery schedules pending destination records and coordinates attach through
  the `RelayCommands` lock so it does not race `resume()`.
- Startup recovery also removes completed, expired pre-ACK, and over-age records.
- Failed destination recovery stores a safe fixed issue code and retry deadline.
  Per-record retries back off through 5, 10, 20, 40, 80 and 160 seconds, then
  cap at 300 seconds; the worker will not retry that record before its deadline.
- `/connect status` now reports pending/running recovery, the fixed issue code,
  and the retry countdown without exposing offer IDs, challenge codes,
  credentials, or raw exception text.
- Updated `docs/specs/agent-device-pairing.md` and
  `docs/specs/agent-public-beacon.md` to match the signed challenge expiry,
  encrypted recovery journal, ACK replay, and the current unreleased bundle
  behavior.

## Cross-cutting issuer-recovery cleanup audit (source slice complete)

- `get_enrollment_request()` intentionally rejects inactive delegations, so recovery cleanup now uses the local `get_recovery_state()` snapshot to inspect the exact durable request and delegation without authorizing a new enrollment. A persisted owner-signed delivery intent is verified before its public scope is returned.
- Cleanup cross-checks the recovery row against the durable round, action, agent/session, destination fingerprint/workspace, issuer, profile, network and credential-category scope, plus the current relay, room, origin, workspace and owner key. Scope or credential mismatches keep the encrypted row and schedule a fixed-code retry.
- A stale row with durable `peer_approved` is deleted without revocation. Any receipt fields with `peer_approved` still false are treated as ambiguous and retained for backoff. An expired/revoked incomplete request with an exact owner-signed credential is revoked by credential ID only when the private directory confirms the exact stored token and destination key; the row is deleted only after that operation succeeds or confirms the credential is already expired/revoked.
- A terminal request that never reached durable approval and has no delivery intent or issued token has no credential to revoke and can be retired. If approval is durable but the exact token is missing, the row is preserved as ambiguous.
- Focused coverage exercises expired and revoked incomplete cleanup, receipt ambiguity, completed-peer preservation, owner/device mismatch without directory mutation, fixed backoff, and redacted aggregate `/connect status`. `test_private_directory.py` plus `test_enrollment_client.py` passed (52 tests).

Validation evidence:

- `python -m py_compile plugins/hub/enrollment_client.py plugins/hub/enrollment_recovery.py plugins/hub/relay_commands.py tests/unit/test_enrollment_client.py tests/unit/test_enrollment_recovery.py tests/unit/test_hub_enrollment_commands.py` → passed.
- Retry regression checks assert the exact backoff sequence and 300-second cap,
  confirm startup does not resume before `retry_after`, and check the safe
  `/connect status` rendering for pending recovery.
- `ruff check` on `plugins/hub/enrollment_client.py`,
  `plugins/hub/relay_commands.py`, and the three touched enrollment/command test
  modules → passed. The command-status test file also passed independently:
  `5 passed in 0.44s`.
- Final enrollment, Hub RPC/commands, relay bridge and peer-session sweep:
  `timeout 180s /tmp/kollab-network-locked-venv/bin/python -m pytest -q tests/unit/test_enrollment_recovery.py tests/unit/test_enrollment_client.py tests/unit/test_enrollment_codes.py tests/unit/test_enrollment_delegations.py tests/unit/test_private_directory.py tests/unit/test_relay_enrollment.py tests/unit/test_hub_connect_enrollment.py tests/unit/test_hub_enrollment_rpc.py tests/unit/test_hub_enrollment_commands.py tests/unit/test_relay_agent_bridge.py tests/unit/test_relay_client_peer_sessions.py`
  → `192 passed in 12.32s` after the import-order correction.
- After issuer cleanup and its added regressions, the same command passed
  `195 passed in 12.74s` on the final rerun. `python -m py_compile`, Ruff on
  the seven changed Python paths, and `git diff --check` also passed.
- The restart test creates a new `RelayClient` on the persisted state directory
  with a fake relay. It verifies exact ACK-envelope replay after the fake relay
  accepts the ACK but loses the response. This is not a real subprocess or
  deployed-relay restart. Mac↔`alzan-prod` live acceptance remains open.
- Code-graph MCP requests returned `Transport closed`; source discovery in this
  update used targeted `rg` searches and direct reads of the enrollment worker,
  status command, and relevant tests. No graph/index-backed impact result is
  claimed.

Next actions:

1. Verify issuer and destination recovery against a real process restart and
   deployed relay reconnect; current evidence uses local fake transport.
2. Continue source-level two-host acceptance, then synchronize the remaining
   canonical docs and walkthroughs against verified behavior.
3. Prepare and publish the corrected release, then repeat the scenario from
   clean installs on the Mac and `alzan-prod`.

## Stop conditions

- Do not call ping/pong, source tests, a verifier status, or a source pilot an
  installed production conversation.
- Do not count a delivered question as a completed answer; verify the answer
  admission and sender-model continuation separately.
- Do not mark direct or multihop routing implemented until runtime call sites
  and route-specific live proof exist.
- Do not publish or claim release completion before package metadata, CI,
  PyPI artifacts and clean installed-host acceptance are verified.
