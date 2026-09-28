# Agent networking goal: work breakdown and progress

Updated: 2026-09-28

This is the execution tracker for the complete networking goal in
`/Users/malmazan/.codex/attachments/1ab030ab-9bc6-415e-9822-e3c55a121d3c/goal-objective.md`.
It records evidence boundaries as well as implementation status. “Partial” and
“in progress” are not completion claims.

Current status: destination recovery source work passes its local regression
sweep. Issuer terminal cleanup is implemented and the 195-test enrollment,
RPC, relay bridge and peer-session sweep passes. Live process restart, full
acceptance, release, and clean-install acceptance remain open.

ETA: no defensible whole-goal estimate yet. The live acceptance and release
gates have not been revalidated.

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
| 2.1 | Issuer enrollment recovery | Source behavior and local regression sweep pass; live restart remains open | Exact approved request resumes only after rechecking owner, relay, origin, room, issuer workspace, recipient key, scope and expiry; it retries the same decision ciphertext. Expired/revoked incomplete rows revoke only the exact owner-signed credential after durable request/delegation checks. Completed peers are never revoked; receipted-but-unresolved and mismatched rows remain with fixed-code backoff. Confirm against a real relay process restart. |
| 2.2 | Destination enrollment completion recovery | Source implementation and regressions pass; capped retry and redacted status added; live relay restart remains open | Persist the stable request before its first POST, then recover the same round through challenge, decision and ACK. Replay the exact signed ACK after a lost response/restart; finish credential import, invite join and attachment idempotently. Retry delay grows from 5 seconds to a 300-second cap; `/connect status` exposes only aggregate state and fixed error codes. |
| 2.3 | Agent conversation and authorization | Live-verified 2026-09-28 (run `0428cb8f`): human-granted send, receiver model and normal tools, correlated result, question/answer, follow-up, cancellation, reconnect; unauthorized, revoked, replay and wrong-workspace rejected at the receiver guard  | Normal Hub/model/tool path, correlated replies, Q&A, progress, errors, cancellation and deadlines work; verify duplicate, impersonation, replay, wrong-workspace, revoked and loop rejection. |
| 2.4 | Peer route integration | Partial; lifecycle and local route tests pass, cross-host proof remains open | Same-host discovery, direct TLS connections, relay fallback and signed bounded forwarding are wired; live tests must still prove cross-host routing and that intermediaries cannot decrypt or invoke ordinary Hub hooks. |
| 2.5 | Discovery, presence and reconnect reliability | Partial | Public/self-hosted DNS contract and quiet local discovery; authenticated expiring records, bounded propagation, truthful offline/revoked status, route changes and restart recovery. |
| 2.6 | Shared-state and capacity limits | Partial | Classify prior load errors; measure the final topology, recovery and resource/backpressure limits; publish only demonstrated operating limits. |
| 3 | Focused regressions and candidate verification | Full unit suite green on the branch; CI (security-scan, standards-check, tests) green on PR #87  | Run focused and cross-cutting suites for touched behavior; retain exact commands and results. Unit and simulated transport evidence remain separate from live proof. |
| 4 | Source-level two-host acceptance | Passed 2026-09-28: run `0428cb8f` through `https://kollabor.ai`, all eleven checks, current source on the Mac and `alzan-prod`  | Current source on Mac and `alzan-prod`: actual models, remote file operation, artifact/hash, correlated reply consumed by sender, follow-up answer, cancellation/reconnect and negative authorization cases. |
| 5 | Synchronize canonical docs and operator guidance | Partial | Specs, implementation ledger, walkthroughs, command help, harness and development skill match the implementation and verified limits. |
| 6 | Prepare and publish a corrected release | Open | Version all packages consistently, changelog and CI pass, normal tag/release/PyPI workflow completes. |
| 7 | Installed-package acceptance | Open | Clean `pip install kollab` environments on Mac and `alzan-prod`; repeat cross-network model/tool/reply flow and required recovery/negative cases. |
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
