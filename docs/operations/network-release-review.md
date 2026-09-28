# Independent networking release review

Review date: 2026-09-27. Base commit: `ee60e381721d1d9e98c8ea6d1a573f350fbca849`.
The reviewed candidate includes uncommitted networking changes. Results here
are observations of that candidate, not proof of an immutable release artifact.
The complete acceptance scope remains in
[implementation status](../specs/agent-network-implementation-status.md) and the
linked networking specifications. This review does not reduce that scope.

## Reproduced issues and required fixes

1. **Input reports success without model submission.**
   `LLMService.submit_human_input()` returns `status=submitted` when the real
   EventBus has no `llm_core.process_user_input` hook and when that hook raises.
   Hook errors are captured in the event result, so the wrapper's exception
   handler does not catch them. Both cases were reproduced with a controlled
   local probe. Require a successful primary handler result; report bounded,
   non-secret errors for absence, exception and timeout. Verify CLI, pipe and
   asynchronous state/RPC reporting, including their eventual failure display.

2. **MCP discovery failure prevents built-in tool loading.**
   `NativeToolsHandler.background_discovery()` reaches `load_tools()` only
   after successful server discovery. A discovery exception marked completion
   with one discovery call, zero schema calls and no native schemas. Keep
   built-in loading independent of external discovery success while retaining
   explicit global/profile native-tool disable and tool-scope restrictions.

3. **Interactive attach input does not grant the requested contact.**
   `HubPlugin._inject_attacher_input()` emits source `hub_plugin`, which the
   human-source allowlist rejects. This callback remains wired to interactive
   `AgentSocketServer` input. A real EventBus/current bridge fixture produced
   zero contact grants through this callback and one grant for the identical
   anchored instruction with source `user`. Route authenticated human attach
   input through a dedicated trusted producer; do not broadly trust internal
   `hub_plugin` events. Preserve structured multimodal content and remote-input
   rejection. Verify the actual attach socket path.

4. **Normal AltView exit retains the generated enrollment code.**
   Real `AltViewSession.exit()` calls `on_suspend()`. The offer view only wipes
   its private code in `on_complete()`, and the Hub's stack push defaults to
   reusable sessions. A lifecycle probe could still reveal its synthetic code
   after exit. Verify cleanup through actual close/suspend/reuse, not only a
   direct call to `on_complete()`.

5. **Bracketed CRLF paste submits a partial enrollment code.**
   Actual KeyParser paste markers were recognized, but the view treated the
   pasted carriage return as Enter and invoked its callback before paste end.
   Pasting must not submit; require deliberate Enter after paste. Cover CRLF,
   fragmented markers, cancellation and code-buffer cleanup.

6. **Offer tests depend on an expired absolute timestamp.**
   Two checked-in tests use `expires_at=1790530000`, which expired on the review
   date. Their failures were reproduced after expiry. A temporary copy changing
   only that fixture to a future timestamp passed. Use a controlled clock and
   deterministic expiration-boundary coverage.

7. **Permanent receiver denial is reported as queued delivery.**
   A root probe using the real bridge, TLS transport and in-process wire reached
   the receiver's `ConversationStore.admit()` and observed one grant-related
   rejection. The sender nevertheless received `state=queued`. Return a bounded
   authenticated application rejection inside TLS, distinct from transient
   delivery failure, without exposing raw exception text. The acceptance harness
   must not treat a queued receipt as proof of receiver denial.

8. **Inbound TLS handshake has no absolute lifetime.**
   `_SessionState` has an activity timestamp, but no creation/deadline field;
   incomplete inbound sessions use idle-only pruning. A controlled clock probe
   retained an incomplete handshake for 2,392 simulated seconds by sending a
   fragment every 299 seconds. Packet, byte and session-count caps exist; this
   result establishes the missing absolute deadline, not unbounded memory.

9. **An expired remote task prevents the next admitted task from running.**
   After the live checksum task expired, authenticated status reported it
   cancelled and both agents' actual runtime state was idle. The next
   human-directed file-only request, `802eec64551e8fb4b2ab3a1da8129530`,
   reached the VPS ledger with the exact authorized content hash but remained
   queued with no new provider/tool trace while the receiver was idle.
   `_stop_active()` invokes normal model cancellation, while `_tick()` blocks
   new remote tasks whenever `cancel_processing` remains true. This suggests
   a bridge-generated cancellation latch, subsequently reproduced and fixed in
   the reviewed source candidate. Recovery must preserve intentional human cancellation/pause and
   must not reset the latch merely because remote traffic arrived.

10. **Command-menu submission does not expand paste placeholders.**
    An isolated input-path review reproduced a slash keystroke followed by a
    large input burst leaving a paste placeholder in `MENU_POPUP` mode.
    `CommandModeHandler._execute_selected_command()` parses the raw buffer,
    unlike normal Enter handling, which expands pasted text first. A fix must
    use the existing paste resolver and preserve command arguments. This does
    not explain the separate full-command live probe that opened Connect device:
    both typed and full-burst `/connect status` reached the status handler in
    the isolated pipeline, and the relevant frozen/current source hashes match.
    The original live command mode and parsed arguments remain unverified.

## Follow-up verification

- The input rejection and built-in loading changes are present in the working
  candidate. A subsequent input/native/MCP/bridge/multimodal run passed **87
  tests**. Review then reproduced a cancellation regression in the new discovery
  structure: cancellation during discovery exits before the completion-event
  `finally`. Preserve that event in an outer `finally` while propagating
  cancellation. The fix still needs independent verification.
- Follow-on root validation passed **133 tests** across human input, native-tool
  handling, MCP controls/integration, the relay bridge, multimodal input and
  local state. The outer discovery `finally` now signals completion while
  propagating cancellation. The actual Unix attach socket uses the new typed
  `hub_attachment` source and the regression confirms a scoped contact grant;
  structured input remains covered. Findings 2 and 3 are resolved at these
  source/integration-test boundaries. Missing/failed primary input hooks now
  return rejection and pipe mode exits nonzero; asynchronous RPC and CLI
  human-visible failure reporting still need acceptance evidence for finding 1.
- The private UI worker fixed normal suspension cleanup and bracketed paste,
  and replaced the expired fixtures with a controlled clock. Root then found a
  pasted Tab could move focus to the visible domain field and expose a synthetic
  code suffix. The worker corrected that control ordering and added real parser
  regressions. Root independently reran **38 passing** connect/contact/AltView
  lifecycle tests and scoped Ruff. Findings 4–6 and the pasted-Tab regression are
  resolved at this component-test boundary in the uncommitted candidate.
- Hub now passes `reuse=False` for the `connect`, `connect-offer`,
  `contact-request` and `contact-review` pushes. An independent probe executed
  eight real Hub command invocations through the actual AltView stack/session
  lifecycle, two per form with changed domains. Each created new views,
  sessions and callbacks; closing removed the registry entry and cleared
  private content. Terminal setup and the render loop were controlled by the
  probe. Live terminal acceptance remains open. Provisioning status/polling,
  remote cancellation and verified-install acknowledgment UI are still missing;
  the current pairing flow only exposes pending/approved/rejected/error.
- The TLS review passed **140** secure-session/registration/conversation tests
  and **33** bridge tests. Certificate pin mismatch and an expired certificate
  were rejected; controlled lifecycle probes confirmed cache invalidation on
  peer registration change, revocation and local disconnect. Those properties
  do not close finding 8 or establish live two-host acceptance.
- A subsequent root run passed **183 tests** across the relay bridge, secure
  session, conversation store, Hub enrollment integration and contact bridge.
  The real TLS bridge regressions now receive fixed `not_authorized` and
  `wrong_workspace` rejection reasons and leave the receiver queue empty.
  A separate root probe through normal `hub_msg` also returned tool failure
  on receiver denial, with no receiver model continuation and no proof artifact.
  Finding 7 is resolved at this in-process encrypted integration boundary;
  live denial and receipt validation remain acceptance checks.
- Incomplete inbound TLS sessions now have a 30-second absolute lifetime in
  addition to the idle timeout. The focused tests exercise pruning after
  simulated activity updates. Pruning occurs on transport activity/admission;
  this does not establish a background timer or exact wall-clock removal.
  An independent probe then delivered real ClientHello bytes as one-byte TLS
  packets through `handle_packet` at simulated seconds 0, 29 and 30. The
  incomplete session remained at 29 seconds, was evicted at 30 despite refreshed
  activity, and never reached application dispatch. Root reran that probe:
  **1 passed**. Finding 8 is resolved at this controlled transport boundary.

## Live pilot readiness

Read-only inventory found both dedicated proof agents still running old source.
The Mac proof environment lacks `cryptography`. The VPS proof source is the
older `source-9cc9801` snapshot without `secure_session.py`; its supplied virtual
environment has the needed provider and crypto packages, but the current process
uses system Python. Both machines have an existing OpenAI OAuth file with mode
`0600`; credential contents were not read and provider validity was not inferred
from file presence. No new account login is required by this inventory alone.

The next live pilot needs an immutable candidate source snapshot, a dependency-
ready interpreter on each host, and controlled replacement of only the dedicated
proof processes. Preserve their existing identity and enrollment state. The
first probe must use human input, the sender model's actual `hub_msg` call, the
receiver model's normal file tool and a correlated reply; direct operator sends
do not satisfy this positive acceptance case.

An isolated source pilot is staged on both hosts. It contains 860 source/assets
files whose hashes were checked after the copy and again on the VPS. Manifest
SHA-256: `d3750da2c51768a1ca4eaff9373db804a392012e83d68e5fe746151df359ba12`.
Staging copied no local network state, credential store or workspace config and
did not restart either running proof agent. It is an unreleased candidate, not
PyPI installation evidence.

In the subsequent controlled pilot window, same-user runtime queries confirmed
both dedicated proof processes were idle with no queued input or pending tools.
Both acknowledged normal Hub shutdown and exited without forced termination.
The Mac relaunched as PID 53727 using a new isolated environment, and the VPS as
PID 2737682 using its existing dedicated environment. The Mac model produced the
startup readiness response. Public-key and workspace identity checks preserve
the preexisting pair; no invitation or credential replacement was performed.

The first source-mode harness preflight then stopped with
`interactive_command_timeout` while waiting for an attached `/connect` result.
No human-directed cross-network model task had been submitted by that run.
Direct same-user status RPC on the Mac succeeds and reports the expected public
beacon online, narrowing the investigation to the attached command path or the
harness parser. The harness worker is investigating with read-only commands.
Its human-input helper also needs explicit workspace/socket binding before a
global same-name presence record can be used as an authorization destination.

### Live model/tool pilot (2026-09-27)

The subsequent task used normal human input on the Mac, the sender model's
`hub_msg`, encrypted public-relay delivery, and the VPS model's `file_create`
and `file_read`. Task `987cc5a3baead87d216b0b82d332556d` created
`relay-pilot-e1745b96bdaf450dbdd4a7a1f1055c91.txt` in the dedicated VPS
workspace. Independent filesystem verification found the exact requested
51 bytes, no trailing newline, and SHA-256
`afb2d86f994e1184f67075ef22a34e3c863536163512519d9cf0205f23613086`.
Both actual provider traces identify `openai_responses` / `gpt-5.6-luna`.
The sender trace contains a matching native `hub_msg` and its receipt; the
receiver trace contains a matching native `file_create` and its session result.

This is partial live acceptance, not a completed round trip. The manual prompt
also requested a checksum; the receiving model attempted `sha256sum` through
its normal terminal tool and received `Permission denied`. No permission was
broadened. After the task's authorization expired, an authenticated status query
returned `cancelled` with `remote task authority changed`. Correlated progress
events reached the sender, but a final successful result has not been verified.
The next positive task will request only file creation/readback; the verifier
will calculate its checksum independently. Provider cancellation/idle state
must be checked before starting another task.

The trace verifier initially missed these actual calls because it required
0600 logs and a historical `session_*.jsonl` pattern. Current owner-owned logs
are 0644, are not writable by other users, and use timestamp-prefixed names.
The harness worker has reproduced the selected sender and receiver traces
with corrected readers; this does not validate every negative/lifecycle check.

Both proof processes subsequently reported `is_processing=false` with no
queued input before the second file-only request. That request was admitted
but remained queued while the receiver stayed idle (finding 9). The original
artifact remains preserved. The test did not reset the model's cancellation
state or inject a human turn to make this recovery problem disappear.
The operator then cancelled only that queued task through authenticated relay
control. A subsequent receiver-ledger read confirmed `cancelled`; selected
provider/tool traces were absent and its unique target file did not exist.
This establishes cancellation before execution, not recovery of the scheduler.

## Acceptance harness gaps

The earlier harness began with operator `/connect send`. Its positive core is
being replaced with human input through the verified workspace owner, actual
sender-model `hub_msg` evidence, receiver-model file-tool evidence, and matched
final-result events. The full revised automated positive run remains pending.
The attached command probe also exposed pasted-input/argument handling that
must be verified separately from operator RPC control.

The independent harness audit found that replay uses bare
`RelayClient.request("message")`, which the new secure-only receive gate rejects
before application admission. That is not evidence of application deduplication.
Wrong-workspace traffic is rejected at sender participant lookup, so it does not
exercise the receiving workspace guard. Unauthorized/revoked cases expect the
old plaintext grant-denial text, which is no longer the secure transport's
observable result. Replace these expectations with evidence from authenticated
current-protocol requests and correlated receiver admission/tool records.

Keep transport replay rejection, application duplicate suppression, sender
validation and receiver authorization as separately verified boundaries.
Same-thread follow-up, provider cancellation with no late tool effect, reconnect,
TLS identity/session binding and installed-package acceptance remain required.

## Evidence collected

- A read-only provisioning consumer audit reproduced keyring overwrite,
  config serialization of delegated credentials, profile resurrection after
  deletion, unrelated global credential precedence, raw refresh-error logging,
  and parent-directory symlink handling defects using temporary synthetic
  state. After the integrator's changes, root independently passed **43 tests**
  across provisioning, the filesystem store, actual profile management and
  OpenAI OAuth polling. Managed credential provenance now prevents keyring
  migration/global-key fallback; profile edits and deletion persist in private
  state; refresh error paths avoid raw provider bodies/exception text; the
  parent-symlink regression preserves the target's permissions. This verifies
  those synthetic consumer cases, not live credential enrollment, successful
  provider access after enrollment, or the full provisioning workflow.

- Recovery review independently passed **144 tests** across the real queue,
  LLM cancellation, message handler, relay bridge, key handling and command
  paste paths. Remote cancellation now owns a generation token; bridge cleanup
  waits for its model/tool cleanup and cannot clear a later human pause.
  Failed cleanup keeps remote admission closed. Completed cleanup tasks are
  pruned. Fresh authorized work after cancellation/revocation is covered in
  source tests; revoked queued work stays cancelled. Live recovery without
  restarting the agent remains unverified.
- Exact command/paste and related input/Connect suites independently passed
  **89 tests**. Menu command execution expands existing paste placeholders;
  fragmented bracketed paste waits for its end marker and deliberate submit,
  has an input-capacity bound, and clears state when leaving or reentering a
  menu. This does not yet close the original live Connect device symptom.
- A recovery snapshot preserves all 860 files in the earlier pilot except
  five reviewed runtime files: LLM coordinator, queue processor, relay bridge,
  command-mode handler and input handler. Its manifest SHA-256 is
  `77b9db446b4667f8f2bc42682b950c41ef6fb6a37fcdf5abc6e7f74725a9f29b`;
  the VPS copy was independently hash checked. It excludes the concurrently
  changing provisioning and enrollment implementation. Both prior dedicated
  proof processes were verified idle and exited through normal Hub shutdown.
  The recovery processes are Mac PID 85044 and VPS PID 3432036. Their public
  keys and workspace IDs match the previous pilot; runtime agent IDs changed
  with the new sessions. The source-bound preflight passed on both hosts.
  The full positive conversation harness was then launched; a launch is not
  evidence of successful completion.

- Input/bridge/local-state/message-handler/LLM/key/mention focused suites:
  **130 passed** using the locked networking test environment.
- Native handler, MCP process controls and MCP integration suites:
  **4 + 3 + 32 passed**; the discovery-exception probe exposed missing coverage.
- Connect/contact UI suites: **16 passed, 2 failed** on the expired fixtures.
  Temporary future-fixture copy: **15 connect tests passed**; contact tests
  passed separately in the original combined run. This does not fix the source.
- Acceptance harness unit suite: **35 passed**. Its stubs do not prove the
  negative paths reach the intended current-protocol guards.
- Later provisioning/harness focused verification: **65 passed**. Provisioning
  tests use a synthetic atomic-store adapter; actual existing-store integration
  and provider usability remain open. Updated trace readers recovered the
  selected live pilot's actual model/tool records as documented above.
- Root independently reran both cancellation-latch reproductions: **2 passed**,
  demonstrating the defect at the bridge and actual queue-processor boundaries.
  Those are reproduction results, not passing recovery acceptance.
- The revised positive-core harness passed **50 tests** independently and was
  frozen with SHA-256
  `b9f8dff454b0aa321e8a5bfac890816ddb8de43a944ee6e792ba61d697537652`.
  Its preflight uses owner/workspace-bound local RPC; interactive command
  acceptance is explicitly recorded separately. The initial source-mode retry
  stopped on the temporary wrapper's `kollab-pilot` program name in `--version`
  output. Renaming a byte-identical proof wrapper to `bin/kollab` preserves the
  ordinary parser contract; this is not an installed-release version fix.
  The next read-only preflight passed on both hosts, reporting the expected
  named agent/workspace and online `https://kollabor.ai` connection. No pairing,
  approval, model or tool mutation was requested. The displayed `0.9.0` comes
  from the isolated environment's installed dependency metadata; the source
  manifest identifies this unreleased pilot and remains the code provenance.
- Scoped Ruff identified import-order errors in `kollabor/application.py` and
  `kollabor/llm/llm_coordinator.py`.

The earlier source reviews did not restart agents; the controlled pilot above
later restarted only the two dedicated proof agents through graceful shutdown.
This review establishes live Mac-to-VPS model/tool execution and exact artifact
creation. The subsequent recovery-snapshot pilot below additionally verifies
the correlated final result at both ends.
This is transport/ledger return evidence; the sender model continuation gap
below prevents claiming the complete conversation requirement.
No new public relay deployment, PyPI publication or clean installed replay is
established by this review. Those remain release gates.

## Successful source-pilot conversation

Run `5e3bb21411d946c682d0f33a1fa49466`, task
`c9ca8c71a3db796d6046e23a4b834559`, used the recovery snapshot above through
`https://kollabor.ai`. Human input entered the verified Mac workspace owner via
`state.send_message`. Both agents used their real `openai_responses` provider
with `gpt-5.6-luna`. The Mac trace has one matching native `hub_msg` call and
tool result for the task. The VPS trace has matching native `file_create` and
`file_read` calls and results. Independent SSH inspection verified exactly 48
UTF-8 bytes in `relay-recovery-5e3bb21411d946c682d0f33a1fa49466.txt`, with
SHA-256 `ea72847ad2b5276d82e5d47f952ed6504e8d7edef311a556539c4f95e51cee55`.
The sending grant and receiving task both reached `completed`; request identity,
workspace addresses, thread and content hash match.

Final result `c07f7b5b582f263812d287e33c39d562` has the task's matching
`reply_to` and `thread_id`. Its receiving-agent outbound event is `delivered`
and sending-agent inbound event is `received`, with equal content SHA-256
`7f98957c96bd9431f4bf0139853080c9e1bcf5cc0b2bd4151f57dd4a8520ca28`.
Private proof artifacts are in the recovery pilot's
`manual-recovery-request.json`, `manual-recovery-ledgers.json` and
`manual-recovery-evidence.json`.

The full frozen harness first stopped before model submission because its
directory lookup still used terminal attachment and timed out. This pilot used
the same reviewed human-input and evidence-reader helpers with explicit owner
RPC control. It does not prove the attached terminal command boundary. The
harness control path is being corrected separately. Cancellation followed by
fresh work without restarting, live negative cases and installed-package
acceptance remain open.

### Sender model continuation remains incomplete

Inspection of the actual Mac session `2609271304-ember-born.jsonl` shows its
last assistant text is `Acceptance request relayed to the designated relay
agent.` The returned VPS result does not appear in that persisted model
conversation. Current `_rpc_event()` calls `_display_correlated_event()`, which
uses `_display_hub_message()` to render a box without normal Hub/model context
injection. Therefore the result's `received` ledger state does not prove that
the requesting agent can reason about it or continue the conversation.

Integrate authenticated, expected conversation events into the established Hub
pipeline with their remote provenance, correlation, deduplication and scoped
authority intact. Preserve human priority and avoid unsolicited reply loops.
Acceptance must verify the sender model actually receives and summarizes the
returned result, beyond the existing receipt, render and ledger assertions.

## Live receiver authorization checks

Run `d96ba32845d14811b1ef632b3e66bd9f` used frozen verifier SHA-256
`307bd6866b042b6228674f846f41d503db6ad420eb5c2d782962c0aa2527f935`
against the recovery-snapshot VPS. Root independently ran its **58 passing**
focused tests before the live run. The probe used a new isolated key and
current `SecureConversationTransport` through the public relay. It preserved
the existing Mac/VPS conversation grant and peer approvals.

- Unauthorized request `1a3d4c242c494e1682b4555a1b64d77a`: authenticated
  `not_authorized`, no receiving task admission or provider/file-tool trace.
- Revoked request `5fe4bc11ab1d4515b8f92b7eeefe1951`: the probe's receiving
  grant was created and removed, its peer approval remained, then authenticated
  `not_authorized` with no admission or provider/file-tool trace.
- Wrong-workspace request `9e75a63fe52e43769cbc29cbf24a2bb2`: authenticated
  `wrong_workspace` from receiver validation, with no admission or
  provider/file-tool trace. This crossed the network to the actual receiving
  guard rather than stopping at sender directory validation.

The replay probe's task `6117ab0be22841219124fef0159f8ace` was admitted once;
its exact semantic retry returned `duplicate=true`, and a changed payload under
the same ID received authenticated `replay` rejection. Its one task completed,
and the trace contains one matching `file_create`/result pair and one matching
`file_read`/result pair. Independent inspection found exactly 52 bytes with
SHA-256 `f1744099742e9f9fad8841eb41fd65e95e5789d76befe2056cb1bf8affc63931`.
The final result matched IDs, addresses, thread and content hash between the
receiver ledger and isolated probe callback. This callback is not a Hub/model
consumer; TLS record replay was not tested by this semantic retry.

The automatic replay verdict remains false: its call-pair predicate incorrectly
applies the network's 32-hex message-ID regex to opaque OpenAI `call_...` IDs,
producing null instead of a boolean. Correct the predicate and rerun before
claiming a clean automated pass. The failed manifest remains unchanged.
Cleanup verified removal of the probe grant, approval, invitation and local
probe state while preserving the pre-existing conversation grant.

## Follow-on verification

- The managed provisioning consumer passed 43 focused tests independently.
  Another 24 ordinary profile tests passed across timeout units, profile switch
  persistence, hot reload, logging, environment detection, application profile
  precedence and login activation. These remain synthetic/source evidence;
  live approved provider provisioning is still required.
- The revised conversation verifier passed 62 focused tests independently. Its
  opaque provider-call-ID check closes the prior replay assertion defect. Root
  then found a mismatch between its sender-consumption fixture and the proposed
  runtime: a Hub reply points to the result/question event ID, while the
  authenticated envelope's parent reply points to the task ID. The verifier
  and runtime must agree on those exact, distinct IDs before a live rerun.
- An instrumented attached client reached registered Hub commands and final
  startup readiness. It held the exact `/connect status` buffer in command
  mode; Enter expanded the buffer and exited the mode, but the traced Hub
  command handler did not run. The proof daemon remained PID 85044, idle with
  zero queued messages and pending tools. Its existing conversation still ended
  at the prior request receipt and contained no `/connect status` occurrence.
  This shows no observed new model turn; it does not establish working attached
  command dispatch. The input worker is tracing and correcting that path.
- Result-continuation review found that setting the active remote task context
  to `None` also removes the marker used by existing remote-origin tool and
  human-command guards. The returned-event continuation needs its own inherited
  provenance: consume/summarize an authorized result without creating fresh
  tool or contact authority; answer only the exact persisted pending question.
  Human turns and explicitly authorized remote task turns must retain their
  existing behavior. This correction and its regressions are in progress.
- Raw traces expose both `conversation_local` and the serialized provider
  `wire_request`. Sender-consumption acceptance must match the exact result in
  both, then match a successful response from that same provider call. A local
  history assertion alone cannot prove the provider received the result.

## Live sender model return

Run `c553cb089c7b4a5aafd93f4334f26b7b`, task
`c1bcb3a631bb49ee425f880365d4acf2`, used source manifest
`c41083436012d30531c5f9d7bcb597cc374c0194fc7e0aba2db2e8f1b3b57617`.
All 860 source files were hash checked on both hosts. This snapshot updates
the relay bridge, two scoped Hub methods and the Hub tool schema; concurrent
enrollment changes and the later answer/TUI fixes are excluded. The prior
proof daemons exited gracefully while idle. The new proof processes are Mac
PID 77707 and VPS PID 4017692, in their existing dedicated workspaces.

The VPS used actual `openai_responses` / `gpt-5.6-luna` with native
`file_create` call `call_8K7TKqrmPXiuASSM4OUlqJsk` and `file_read` call
`call_mSdyzljiYp6KwhtOsKyx158j`; each has a matching tool result. Independent
SSH inspection verified 56 exact UTF-8 bytes in
`relay-acceptance-c553cb089c7b4a5aafd93f4334f26b7b.txt`, SHA-256
`713be9582b7099ef990767baa0f5221b2a933e073bc0fdb75267e9286a4b09c9`.
The task completed. Final event `dd074f81a8b5b9beba623aea60465bfb` is delivered
at the VPS and received at the Mac, with matching addresses, task correlation
and body SHA-256
`3b0fab1fd35575083bc1c1a4a9195dd4bb74798404d240a2d658de760306443b`.

The actual Mac session `2609271403-synth-loop` includes the returned event.
Its raw local conversation and serialized provider `wire_request.input` both
contain the result and exact event/thread/peer context. The same successful
provider response reports the unique run marker, remote file path and exact
content. This supplies the sender model consumption evidence missing from the
earlier pilot.

The frozen verifier still waits on a false-negative assertion: AgentHud adds
two spaces to multiline continuation text, so the unformatted body does not
match as a raw substring. Root verified that removing precisely that known
formatting layer preserves the full body at both local and wire boundaries.
The verifier needs a canonical rendered-body assertion and regression using
the actual AgentHud formatter; its original run verdict must remain unchanged.
Private independent file/tool/ledger evidence is saved in this pilot's
`positive-return-evidence.json`. This does not close live same-thread answers,
cancellation, enrollment, routing/scale, or installed-release acceptance.

Latest independent source checks also passed 167 bridge/store tests and 298 TUI
tests with 20 subtests. The TUI change accepts both CR and LF (`Ctrl+J`) menu
submits and prevents declined menu submits falling into generic message input.
The running snapshot above does not yet include that TUI correction.

## Automated core and receiver guards pass

Fresh run `aff695ddf22142448c9225226bad591e` passed the positive conversation
and all four selected receiver checks. Frozen verifier SHA-256
`aa94edd0f3687bcf0dc1dfb72e037651755242f26338c6d37fe1e99184dbaff1`
passed 63 tests independently, including real AgentHud multiline formatting.
The earlier `c553...` manifest remains failed; its cleanup revoked that run's
outbound grant and events, so subsequent ledger readback cannot treat them as
currently authorized received events.

The fresh run used source manifest
`8cca62db89e042051b890bff0baf6683a2231a9abc8ef116bb74325b0bad913f`,
with all 860 files hash checked on both hosts. This adds the verified answer
provenance and CR/LF TUI changes to the prior result-routing slice, while
excluding concurrent enrollment, mesh and cancellation-observability work.
Proof processes are Mac PID 24635 and VPS PID 4161261. Existing keys and
workspace identities remained unchanged.

- Task `8fd5a29aa98d1df9e221b218b4e382f2` passed through actual sender
  `hub_msg`, receiving `openai_responses` / `gpt-5.6-luna`, native `file_create`
  and `file_read`, matching tool results and a completed receiving task.
  File `relay-acceptance-aff695ddf22142448c9225226bad591e.txt` is exactly 56
  bytes, SHA-256
  `65bf643f1340f90544042c469249b7e11239d3699f823554338c2370592a63cb`.
- Result `c12605168dd07c71b5bbf2d28e3b264d` is delivered at the receiver and
  received at the sender, body SHA-256
  `438a739b58debea26322a12a6af93e3db4a03c523ac7a2a38d79c9c338aed014`.
  The exact body and correlation envelope appear in one sender local request
  and its serialized provider request. The same successful provider call
  reports the unique marker, path and content. The serialized local trace
  retains HUD metadata but not all original Hub metadata fields; exact IDs
  are verified through the explicit context envelope.
- Unauthorized request `08498c0b6cad40359b94c9fc9ed28350`, revoked request
  `e4c774bc68ca4922b8f195dc03d90215`, and wrong-workspace request
  `4fab7de147d640519a350ec483b7f4ef` reached authenticated receiving guards
  and were rejected without task admission or provider/tool execution.
- Semantic replay task `3411aeff305e407b930b7363eb39c625` was admitted
  once. An identical retry was marked duplicate; a conflicting body under the
  same ID was rejected as replay. Exactly one native create/read pair ran.
  The replay observer is an isolated secure client, not a Hub model; TLS
  packet replay was not attempted.
- Cleanup passed: this run's send grant and probe resources were removed or
  revoked, while the pre-existing conversation grant was preserved.

The manifest is under the second return pilot's
`acceptance/relay-agent-conversation-aff695ddf22142448c9225226bad591e/manifest.json`.
The displayed package version remains dependency-environment metadata `0.9.0`;
the manifest identifies this unreleased source snapshot. Attached command UI,
same-thread question/answer, live cancellation/reconnect, enrollment/recovery,
peer routing, capacity and clean published-install proof remain open.

## Attached command registration blocks the terminal path

Actual attached `/connect status` checks against the same 860-file snapshot
opened the private enrollment view on both hosts. The local check timed out;
the remote check reported graceful-detach cleanup pending. Subsequent process
inspection found no matching attached clients on either host. Both proof
daemons retained their PIDs, with processing false, queue zero and pending
tools zero. The core conversation verdict above is unchanged.

Independent reproduction identifies startup registration, rather than lost
paste arguments: `Application` discovers AltViews before initializing Hub.
`ConnectAltView` advertises `plugin_type="connect"`, `category="system"`, so
the integrator registers `/connect`. Hub's later registration is rejected as
a name conflict. The final handler belongs to `altview_integrator`, and the
Hub handler is absent. Earlier input tests registered Hub directly and did
not exercise that startup order.

The enrollment worker owns the correction: make Hub-invoked enrollment and
contact views internal, use the existing internal-view registration behavior,
and add startup-order coverage for status, private entry and sibling views.
The correction still needs actual attached-terminal verification on both hosts.
Evidence lives in the second return pilot's `ui-command-evidence.json`,
`ui-diagnostics/`, and `connect-registration-evidence.json`.

### Registration correction verified through both attached terminals

Root independently passed 33 Hub-enrollment/connect-view/contact-view tests,
including discovery before Hub registration. A fresh viewer snapshot changes
only the two AltView files' three `category="system"` values to `internal`.
Its 860 source files were hash verified on both hosts; manifest SHA-256 is
`277babfd63819fed9946dba4f93858e79bcdc9da61705cbbbe4fa4373b6a4a78`.
The two existing daemons were not restarted or changed.

Actual attached `/connect status` now displays `online`, `https://kollabor.ai`,
the expected public key, workspace path/ID, and existing agent identity/ID on
both hosts. Both viewers report normal detach. Root parsed exactly one full
displayed status frame per host and checked it against a fresh, owned real
workspace probe. Local agent `dcacb73bba8d` and remote agent `bb9b6484a401`
match the daemons from the passing conversation run.

The frozen verifier still records `relay_status_unavailable`: its plain-output
parser anchors `beacon:` to a line start, while the TUI prefixes the frame with
`info:` after a rendered status line. Keep those original failed verdicts.
The harness worker owns a narrow attached-frame parser and regression; root's
independent proof is separate. Private artifacts are in
`kollab-viewer-pilot-480v0uws/ui-independent-evidence.json` and
`status-output-local.txt` / `status-output-remote.txt`. This closes the observed
status-command registration bug, not private code enrollment acceptance.

## Next conversation and cancellation verification

The first same-task question/answer verifier candidate (`9ffe64f1865f7575...`)
passed 68 tests independently. Review then found two asynchronous evidence
boundaries that require waits: the sender's tool trace can lag the sent ledger,
and the human answer must follow the sender model's actual consumption of the
question. The harness worker is adding bounded evidence waits and the attached
status-frame correction before a live question/answer run. No live result is
claimed for that candidate.

The cancellation instrumentation candidate exposes sanitized provider/tool
phase, task ID, operation generation and cleanup state through the existing
owner state snapshot. Review caught rejection of mixed-case provider tool IDs;
that was corrected at the executor, coordinator and snapshot boundaries.
Root's targeted observability, bridge, queue, tool, provider and local-state
suite passed 174 tests. The worker is isolating this slice against the running
source snapshot before staging. Active-provider cancellation and same-process
recovery still require live proof.

### Isolated operation snapshot and corrected verifier

Root staged the reviewed operation-tracking slice with the attached-view fix
and the existing private-room status wording correction. All 860 source files
were hash verified on both hosts. Source manifest SHA-256 is
`2942e10178a91a354c37a00d6acf751713f5543730f5b87a43629f311503bfe4`.
An independent 174-test run imported the actual isolated candidate modules,
with their source paths asserted, and passed. An earlier isolated snapshot's
single stale-copy failure is retained separately; it was not deployed.

Only the dedicated idle proof daemons were gracefully stopped and replaced.
Mac PID 47512 and VPS PID 346501 preserve the existing public keys and workspace
IDs. Both report online through the public relay and expose an actual completed
provider request through the new state snapshot. Restarting to install this
instrumentation is not cancellation-recovery evidence.

The frozen question/answer verifier SHA-256
`d307ad8d3f9443faea4525b746d3368040c8bf6c9ea99ed196472e763e9ada76`
passed 79 independent tests against that isolated source. It waits for the
initial tool receipt and for the sender's provider-wire consumption and
successful response before submitting the human answer. It also recognizes
one complete attached status frame while rejecting incomplete or conflicting
frames. Tests SHA-256 is
`e7be5933ba305cb84160178752058d0f08cb54567ea7c94636b4cd5c7b86db45`.

Live attached-command and same-task question/answer verification has started
against this snapshot. Its result remains pending. Concurrent enrollment,
provisioning and peer-routing edits are excluded from this live snapshot.

### Live UI and core pass; question/answer setup fails

Run `c2af5e59e5c743259dec129747f5987e` against source manifest `2942...`
passed attached `/connect status` on both machines through the frozen verifier.
It matched the current daemon identities and workspace paths/IDs and returned
online status. The complete core flow also passed again on this snapshot:

- Task `f58cc6f58bf0b7063812b83475cc79d1` used the sender model's native
  `hub_msg`, receiver model's `file_create` and `file_read`, and one correlated
  completed reply consumed in the sender's actual provider request and response.
- The created file is 56 bytes with SHA-256
  `48fe71c8827abfe83932971e8f9484e5f146cfc28af30dabf005f6185659e037`.
- The result event is `78fcbc9f52664ed5ec1b729f4ea1d383`, body SHA-256
  `da80b992ebd3000ec9b390708412305902ae0ddaa3a4315540acd5c48b0cc4d3`.

The subsequent same-task question/answer check did **not** pass. Its first
request timed out with `sender_hub_tool_missing`, before the verifier obtained
a consumed sender authorization grant for that request. Root has assigned
read-only diagnosis of the actual prompt, grant and provider trace. This is
not evidence of successful question delivery or answer handling. Preserve the
original failed overall run. Cleanup completed and preserved the pre-existing
conversation grant.

The complete manifest is in the operation pilot's
`acceptance/relay-agent-conversation-c2af5e59e5c743259dec129747f5987e/manifest.json`.
The manifest's endpoint preflight UI fields describe the earlier preflight;
the separate `checks.ui-command` and top-level acceptance boundary record the
subsequent verified attached-terminal result.

### Q&A dispatch cause verified in the real sender trace

Independent inspection of daemon session `2609271455-griffin-forge` found the
exact Q&A authorization in the sender store: grant
`7b4f3f0926b2215f2575367c0fc43dfc`, 1,185-character purpose, SHA-256
`d5008070a98884d16672b230a09257c2dcb2fb88b93f3c1aa474c1ad5bc95910`.
It was revoked by the failed run's cleanup. The model's five subsequent native
`hub_msg` calls all have matching rejected tool results:

- Two calls used `kind=question` and the clarification text before any remote
  task existed. The runtime requires an active receiving task for that event.
- Two calls used `kind=message` with the full 1,316-character human input,
  including `Please ask <address> to`, instead of the authorized purpose.
  The runtime rejected the exact-request mismatch.
- One additional `kind=question` call used that same full human input and was
  also rejected. No Q&A task reached the receiver.

The native `hub_msg` schema incorrectly describes `question` as asking the
remote worker a follow-up. The runtime permits the receiving worker to ask its
authenticated sender for clarification. Ready-grant context also uses the
ambiguous phrase `the exact human request` and adds punctuation after the
purpose. The assigned correction makes initial dispatch explicitly
`kind=message` with canonical escaped arguments and the exact stored purpose;
it preserves all authorization checks. The correction is not yet verified.

Private root evidence is `kollab-operation-pilot-s3jf1__5/qa-dispatch-diagnosis.json`.
It records exact comparisons, bounded metadata, tool rejection categories and
source/trace hashes without copying task text or credentials. The 15:10
conversation files belong to attached viewers; they are not the daemon's
model trace. An earlier worker assertion about mismatched stored purposes was
incorrect and is superseded by this direct comparison.

### Cancellation proof preparation and remaining route scope

Root independently checked the revised private cancellation driver's SHA-256
`43e9fbacf50a917890cd9e65c0e8cdef31ec7e57a05cc8251967268feeb9cb51`
and passed its 33 synthetic checks. It ignores retained token-display counts
when deciding idleness, requires the exact task and provider operation before
cancelling, checks matching cancellation cleanup, then requires a fresh real
file-tool exchange on the same daemon PIDs. Its unique cancelled target is now
actually requested by the task. Trace assertions cover that requested
create/read pair and correlated result events, not arbitrary tool names.
Only this run's exact outbound grants are withdrawn. The original driver is
retained. No live execution or cancellation-recovery result is claimed yet.

Direct and LAN routing remain required. Forwarding over the existing shared
relay roster alone does not satisfy them. Work is split between an opt-in,
minimal signed LAN locator module and direct/multi-hop carrier integration.
The existing endpoint designation key and relay peer key are different;
reuse of the endpoint listener requires a verified two-key endpoint binding
and an authenticated, bounded opaque forwarding callback. That callback must
not reach ordinary Hub message hooks, model execution or administrative RPC.
LAN locators must not broadcast private room membership, workspace paths/IDs,
agent rosters or credentials. Integration and live proof remain open.

### Capacity instrumentation check

Root independently ran `tests/unit/test_relay_capacity_measurement.py` against
the current candidate: 4 passed. Measurement script SHA-256 is
`9d23bdeda3b4e4e708fcce756d2930b74d852481672b72e4acf45ae4edb30011`;
test SHA-256 is
`e2790a29d09809d6dd6af54a43786cf931f1c172041b48114da9d37732e755d0`.
The focused checks cover explicit pair approval, source/topology labeling,
bounded error categories and process telemetry. No live load was generated.
The earlier 31 high-rate client errors still need a classified live rerun and
an evidence-backed operating envelope; the unit result does not establish
capacity, server saturation or production topology.

### Guidance candidate and second live Q&A boundary

Root applied only the three guidance-file hunks to a copy of snapshot2942.
New manifest SHA-256 is
`c39f5ba7d4b0c9c5d7386637b39acf28609105ee897d4412b323878e861d001b`;
all 860 files were hash-verified locally and on alzan-prod. The isolated source
passed 177 focused tests with imported production paths asserted inside that
source. Mac proof PID 47512 and VPS proof PID 346501 shut down gracefully;
replacement PIDs are 37779 and 1048045. Existing keys and workspace IDs stayed
the same. Runtime agent IDs changed to `3c9d636273ae` and `4caac120841e`.

Run `093d156e8d7d4f0988cd44c929d7796c` again passed attached `/connect status`
on both hosts and the full actual-model file-tool exchange. Task
`d3e63d22695918530c966f184f93820a` completed; its 56-byte remote artifact has
SHA-256 `8d86a7603e25e9f0200219afbf8d207c88ff23911e8598b371b5e358e6da5008`.
Result `7b6055fdce1fc2e13926f83da1fb61a6` reached the sender's real provider
request and successful response. This remains a source pilot, not a release.

The new guidance also got Q&A task `3b085f7d78f0288cdbd765608a1e65f1`
admitted and question `99b79bced85a746a9b69a15f75b44ab5` delivered to the
sender ledger. The Q&A verifier stopped before submitting a human answer with
`question_event_correlation_invalid`. Inspection identified an incorrect
verifier requirement: question conversation-event state intentionally stays
`pending` for answer admission, while transport delivery is recorded in
`outbound_queue`. The verifier demanded `delivered` in the question event.
The correction must independently prove the outbound delivery receipt and
both pending question records, including exact addresses, IDs and hashes.
Do not change runtime pending-question semantics to satisfy the verifier.

Failed-Q&A cleanup also withdrew the outbound grant but left the receiver
waiting. Root cancelled only that exact task through the normal workspace-owner
`/connect cancel` path and verified its receiver state became `cancelled`.
No daemon was restarted for cleanup. The worker is correcting cleanup order
and adding regressions. Full Q&A remains unverified.

Private evidence is under `kollab-guidance-pilot-rnwthlhw`: the run manifest,
`focused-test-evidence.json`, `qa-correlation-diagnosis.json`, and
`qa-cleanup.json`. Preserve the failed run; a later pass is separate evidence.

Root subsequently read the exact Q&A transport row read-only on the VPS:
question `99b79bced85a746a9b69a15f75b44ab5` has `outbound_queue.state=delivered`,
kind `question`, and thread `3b085f7d78f0288cdbd765608a1e65f1`.
`qa-question-delivery.json` preserves this evidence separately from pending
answer state and the later explicit task cancellation.

### Earlier peer transport source review

The native worker's discovery service and shared locator module independently
passed 53 tests against their frozen files (loopback UDP is local-only). The
current `peer_transport.py` SHA-256 at this source review was `c80e20a55f4fe634019f14ece342c174b9a3875363b58a9d774b53b5cd0078a4`. It now
contains a defaults-off `PeerMeshRuntime` with opt-in discovery and direct TLS
paths; the runtime owner files `plugin.py` and `relay_agent.py` did not yet
instantiate/start/close it when checked (no `PeerMeshRuntime(...)`,
`peer_mesh.start`, or `peer_mesh.close` call sites). The peer worker is wiring
that lifecycle.

Review found `asyncio.open_connection(address, port, port, ssl=...)` in the
direct dial path, which passes an unsupported third positional argument. The
worker was asked to correct and test the real dial call. There is no direct,
LAN, relay-mesh, or multihop live acceptance result yet.

### Peer mesh integration and verifier checkpoint

The later shared source now constructs and starts `PeerMeshRuntime` in
`plugins/hub/relay_agent.py` (lines 272 and 303), closes it at line 340, and
refreshes its directory at line 363. The secure receive dispatcher is at line
1288. Source hashes at this checkpoint:

- `plugins/hub/relay_agent.py`: `bdac3956345addf90f44e87edf1551911dcec65f3dd8409b6e6b97dddc3621f5`
- `plugins/hub/plugin.py`: `6bb8bd97abd6a3e2addab270f5f5d55ff64a5c3926318531fb23d1df5c7e53b6`
- `plugins/hub/peer_transport.py`: `79f2120063bcc037d4aec273cc5a5d18d3e41781110c42a5fe0beca05bc3381c`

Root ran the verifier and bridge suites together with
`timeout 180s /tmp/kollab-network-locked-venv/bin/python -m pytest tests/unit/test_verify_agent_conversation.py tests/unit/test_relay_agent_bridge.py -q`:
145 passed in 11.58 seconds. This includes
`test_remote_directory_requires_secure_conversation_transport`, which checks
that directory queries use `SecureConversationTransport` and that an unwrapped
directory request is rejected. The verifier files match the acceptance worker's
reported hashes: `verify_agent_conversation.py`
`8c07ee1e82dcb2edafe3cb3b1c57ddb7b49f008e518c160a1d2b35868f2cae36` and
`test_verify_agent_conversation.py`
`405be7432856bfe3b6fa9756dbf6643b1c9abe16d529756070b5a7bd31f2c331`.

The first combined run exposed three test mocks that assumed directory lookup
was a raw relay request and one assertion that required a handshake despite a
cached secure session. The peer worker corrected those fixtures; the focused
regression selection passed (4 passed), then the complete verifier-plus-bridge
run passed. This is source-level evidence only: no listener was started and no
direct, LAN, relay-mesh, or multihop live acceptance result is claimed. Peer
transport lifecycle, route-specific coverage, enrollment recovery, the real
Mac-to-alzan-prod flow on the integrated source, capacity bounds and PyPI
verification remain open.

### Peer routing regression suite

Root independently ran the verifier, bridge, peer transport, locator and
discovery tests together:

`timeout 180s /tmp/kollab-network-locked-venv/bin/python -m pytest tests/unit/test_verify_agent_conversation.py tests/unit/test_relay_agent_bridge.py tests/unit/test_peer_transport.py tests/unit/test_peer_locator.py tests/unit/test_peer_discovery.py -q`

Result: 202 passed in 12.51 seconds. Independent checks also passed for Ruff
on `messenger.py`, `peer_locator.py`, `peer_transport.py` and the three peer
test files; `py_compile` passed on the peer source and transport test files;
scoped `git diff --check` returned no errors. Current files match the peer
worker's hashes: `peer_transport.py`
`79f2120063bcc037d4aec273cc5a5d18d3e41781110c42a5fe0beca05bc3381c`,
`messenger.py` `4f84ad7841a6d68b0bcae96dc4b3c11775a883fae31cfa764dfc5115d7441f9b`,
`test_peer_transport.py` `9fd2dafc81b08d59e74871abbaed881f53baba927dc044c9879088a9174b0cdd`,
and `test_relay_agent_bridge.py`
`b57a53bcc1b40b1f52f850bdd12c3de2bbf9de3ca492ee72aa9d6f19a812fa59`.

The suite exercises a loopback TLS listener, candidate-only loopback UDP
discovery, and encrypted A-to-C-to-B forwarding through in-process clients,
including replay rejection. It does not prove a real cross-host route, live
LAN discovery, process restart recovery, relay failover or PyPI installation.

### Issuer recovery regression sweep

Root independently reran the broader enrollment, private-directory, RPC,
bridge and relay-enrollment suites after the recovery fixture was corrected:

`timeout 180s /tmp/kollab-network-locked-venv/bin/python -m pytest tests/unit/test_enrollment_*.py tests/unit/test_hub_connect_enrollment.py tests/unit/test_hub_enrollment_rpc.py tests/unit/test_hub_enrollment_commands.py tests/unit/test_private_directory.py tests/unit/test_relay_enrollment.py -q`

Result: 125 passed in 3.52 seconds. The preceding run had 124 passed and one
fixture failure: the command test supplied a `SimpleNamespace` instead of the
production `_ProvisioningPlan` dataclass required by the encrypted journal
serializer. The implementer corrected the fixture with a real plan and
`ProfilePreferences`; the rerun passed. This is local source evidence only. It
does not prove a real process restart or live relay recovery. Destination-side
install-ACK replay, orphaned credential cleanup, cross-host acceptance and
installed-package validation remain open.
