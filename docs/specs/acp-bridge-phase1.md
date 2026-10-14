# ACP bridge implementation checkpoint

2026-10-13: the initial implementation was committed as `9a123150`. The
subsequent source audit identified 13 concrete repair items before a replacement
goal was created. Those source repairs are now implemented in separate scoped
commits, recorded below. No tests, builds, lint,
adapter/client runs, browser runs or deployment were performed during this pass,
by explicit instruction. Earlier results below describe an earlier checkpoint
and do not qualify these changes.

## Current qualification pass (2026-10-14)

Executable qualification was subsequently authorized. Full section 9 remains
open; the evidence below distinguishes source checks, controlled SDK runs, real
models, rendered UI and the off-box network boundary.

Verified fixes and boundaries:

- Startup: `ea95af7f`, `d003ee71`, `94511e2f`, `359912ce` and `f1136882`
  resolve pinned managed adapters, preflight before spawning, wait for actual
  daemon readiness and retain actionable errors in the launcher. Claude's quota
  text survives opaque request errors. Missing executables leave no candidate
  daemon or ready engine session. Named profile strings use authoritative daemon
  profile state. The engine/startup suite passed 232 tests; a focused input/state/
  assistant/startup run passed 96. Counts overlap with later focused runs.
- Hub: `8dc42998`, `09cce6ab` and `29f1c38c` repair ordinary request admission,
  distinct request FIFO, completion-owner validation, question-back imports and
  public wait-state tags. Local collaboration, MCP stdio/socket, native reply and
  permission regressions pass. Real Codex adapter 2.1.1 handled an ordinary local
  Hub request, called `hub_status` and `hub_agents`, streamed `ACP LIVE CODEX OK`
  and returned one final message plus one hidden end frame on its original
  request/thread. The corrected application probe exited zero with no owned root
  PIDs. Trace: `/tmp/kacp-live-xVqw6o/codex-application.jsonl`; summary:
  `/tmp/kacp-live-xVqw6o/codex-live-summary.log`.
- Native Hub lifecycle: `c65007ef` publishes idle when the owned input chain
  settles, including provider errors and cancellation. A model response cannot
  publish idle while tools, queued input or a turn lock remain; parked waiting
  states are preserved. All 20 tracked cases pass after the fix; the broader
  native/remote/Hub/CLI/queue/socket run passed 379 tests, and five unchanged
  private reproduction cases pass. `8d8babd9` fixes the native roster formatter's
  undefined label and renders harness labels and ACP permission waiting state
  in both native Hub commands. Its adjacent run passed 142 tests. Evidence:
  `/tmp/kacp-live-xVqw6o/roster-checkpoint.md`.
- Native provider cleanup: `a1e0b14a` awaits OpenAI's async SDK close before
  dropping the client; Azure inherits the fix. Actual SDK lifecycle tests cover
  closure, repeated shutdown and shutdown before initialize. The provider run
  passed 115 tests. A full native application run sent one controlled loopback
  HTTP 400 request and observed working → provider failure → idle, then verified
  the retained SDK client was closed before daemon exit with no unawaited-close
  warning. Exit was zero, cleanup took 0.184 seconds and no owned processes,
  socket, presence file or fault server remained. This is actual native lifecycle
  evidence, not a successful vendor/model turn. Evidence:
  `/tmp/kacp-native-cleanup.wf4nn29s/README.md` and `summary.json`.
- Outbound: real Codex cancel, negotiated steering, resume and missing-login
  failure gates pass. Actual permission approve, deny and the complete 300-second
  timeout pass; only approval creates the owned sentinel. Evidence is in
  `codex-protocol.jsonl`, `codex-permissions.jsonl` and their summaries under
  `/tmp/kacp-live-xVqw6o`. `453ca014` rejects non-offered option IDs and empty
  eligible option tables. `f77d3734` journals observed partial output by owned
  request/turn and saves it once on cancellation/error without inventing vendor
  tool status. The latest six-file outbound run passed 87 tests in 35.35 seconds.
- Claude Code: after the reported reset, adapter 0.89.0/native SDK 0.3.293
  completed an actual `TerminalLLMChat` → ordinary owned Hub request →
  `hub_status`/`hub_agents` → visible `ACP LIVE CLAUDE OK` and hidden end frame,
  on request `2379939468cf43a1ba4e840c54e9ed6a` and its original thread.
  Exact one-shot approval created the owned Write sentinel; denial left its
  sentinel absent. The corrected lifecycle run used session
  `61bef795-e057-4d8c-abc5-9d95f8d1917d`: a birth-checked condition worker was
  running with an active pending admission before cancel and steering. The
  vendor card stayed `pending`; no `in_progress` status was invented.
  Connection 1 prompt `2` returned `cancelled` after `session/cancel` in
  0.012 seconds, preserving the session/adapter. Prompt `3` and
  `_session/steering` request `4` returned `ACP CLAUDE STEERING OK`/`end_turn`.
  Connection 2 `session/resume` request `1` restored the same session with
  zero replay wire/display updates; its prompt `2` recalled the same phrase.
  Those numeric IDs belong to separate connection namespaces. Evidence:
  `/tmp/kacp-claude-postreset-o0ehk6wg/qualification_summary.json` and
  `/tmp/kacp-claude-control-dn6pbyvi/aggregate-checkpoint.json`.
  The initial standalone-sleep fixture was rejected before execution; a
  subsequent observer incorrectly waited for an `in_progress` frame while the
  supported condition command ran to its owned deadline. Both mistakes remain
  separate from product failures and successful cancellation proof, with
  cleanup checks under `/tmp/kacp-claude-lifecycle-7f50mjhw` and the first root.
  The actual production permission timeout then waited 300.001213 seconds on
  tool `toolu_018jAE1LYMvHrXwtSED84TTb`. Agent permission RPC `0` was answered
  with its exact offered `reject` option. The sentinel stayed absent; original
  request `630dab3eeed04ca984adce3c6f442a07`/thread
  `owned-timeout-claude-thread` received one waiting notice, then
  `ACP CLAUDE TIMEOUT DENIED` and its hidden `end_turn` frame. Pending
  permissions/cancel tasks drained. Evidence:
  `/tmp/kacp-claude-timeout-tw3kjdn1/independent-postcheck.json`.
  Missing login passes at this pin's normal validation point: an empty-store
  `session/new` succeeds, then the single controlled prompt `2` returns
  `-32000 Authentication required`; the runner preserves the code and adds
  local login guidance. No model answer, tools or `authenticate` RPC occurs.
  Evidence: `/tmp/kacp-claude-auth-rejection-opiw06zd/independent-postcheck.json`.
  The new-session-only probe at `/tmp/kacp-claude-missing-auth-r0i56ool` is not
  an auth-rejection pass, and the old empty summary's SyntaxError remains a
  setup failure. Positive runs copied the existing valid keychain OAuth
  subtree into private storage; missing-auth runs copied no credentials.
  Child-only command guards used the vendor's existing file fallback; every
  observed guard refusal was a nonprivate read, with zero observed mutation
  attempts/delegations. Earlier summary counter names combined refused reads
  and writes; `guard-operation-classification.json` separates them without
  rewriting frozen evidence. Original auth stores stayed byte-identical,
  private namespaces stayed absent and private auth directories were removed.
  All observed owned PID/birth identities are gone; closed runners have queue
  zero, processing false and no process/worker. Manifest/source hashes and
  protocol/process evidence do not imply packet capture or a zero HTTP count.
- Cleanup: `52fb41d1` recovers same-user, marked descendants orphaned before
  observation, including double-fork/`setsid` children born during prompt and
  close. Wrong-nonce outsiders survive. The real Codex run removed its four
  observed process identities. Its login shell discarded the inherited marker:
  cleanup is not OS containment, and unseen children that strip the marker or
  change user identity remain outside this proof.
- Missing auth and close: `741e1a8d` wakes only a live queue consumer and
  settles remaining owners after teardown, including an unread shutdown
  sentinel, deadline cancellation and admissions racing close. The processing
  predicate is unchanged. The runner/profile/orchestrator/application lifecycle
  run passed 85 tests; five controlled SDK Hub/application integrations passed;
  root independently reran 11 overlapping lifecycle cases. Real Gemini CLI
  0.63.0, on the exact bridge bytes now committed in `741e1a8d`, answered
  `initialize` id `0`, then rejected `session/new` id `1` with `-32000` and
  `Gemini API key is missing or not configured.` The runner preserved that code
  and reason, then closed with queue size zero, processing false, no worker,
  connection or process, and no surviving owned PID/birth identities or signals.
  No credentials were copied, and no `authenticate` or model prompt was sent.
  No auth selection was configured and provider/Google/Vertex credential
  variables were absent. The supported file-storage flag kept lookup out of the
  OS keychain. A private user settings file disabled usage statistics, telemetry
  and automatic Node
  heap configuration, leaving one local `data:` fetch visible to passive Node
  hooks. Those hooks are not packet capture. Evidence:
  `/tmp/kacp-gemini-close-fixed-8ejufjbt/close-repair-checkpoint.json`,
  `qualification_summary.json`, `protocol.jsonl` and `independent-postcheck.json`.
  The earlier unsupported `--ignore-env` helper setup stopped before an
  initialize response and is neither a product failure nor an auth pass:
  `/tmp/kacp-gemini-missing-login-kttdsy1n/setup_failure_summary.json`.
  The corrected empty-home run reproduced the close bug and observed two
  anonymous Clearcut HTTPS initiations to `play.googleapis.com`; those nonzero
  observations remain in
  `/tmp/kacp-gemini-missing-login-corrected-osk2_bxu/qualification_summary.json`.
  The successful Gemini model/tool/permission/cancel gates remain open.
- Inbound: `f2e293e1`, `362aea82`, `9715049c` and `14364d69` cover SDK variants,
  request-specific cancellation, attach ownership, saved replay, protocol-only
  stdout and complete handle cleanup. The full 17-case real CLI/daemon/stdio
  matrix passed in 189.52 seconds, with 24 protocol traces and no stray stdout
  lines. After the stopped-history fix, `61504527` added a real active cancel →
  close → fresh load check: partial text, pending/in-progress cards, typed diff,
  falsey output and cancelled parent metadata survive; initial, replacement and
  restored adapter PIDs exit. This new case and the directly affected normal
  restore case passed in 34.89 seconds. Artifacts:
  `/tmp/kacp-inbound-final.33P2UP` and
  `/tmp/kacp-inbound-sdk-stop-fixed.nhBMfF`. The earlier full run predates the
  stopped-history source fix; it is not a claim that all 18 cases were rerun.
- Actual client: corrected private fixture traces from Zed Preview
  1.24.2 now show session `b2bf547d357341f296bda2837a7a7210`, normal prompt
  `37acb491-2ddd-4a8f-8a86-db2c17eaf25b`, opaque `vendor-once` approval,
  a completed tool card with an absolute owned diff path, `OWNED SDK ANSWER`
  and `end_turn`. Prompt `169ce732-1c03-47a1-9836-022c518df30a` used a distinct
  tool ID, streamed partial output and retained vendor status `in_progress`
  with raw output `0`. Actual UI Stop sent `session/cancel`; that prompt
  returned `cancelled` and the controlled SDK recorded its cancel marker.
  No completed/failed vendor tool status was invented for the stopped card.
  A genuine attached client offered no `allow_always`; UI Deny Once selected
  `vendor-deny` and returned `OWNED SDK DENIED`/`end_turn`. Reload dropped the
  old attached stdio/proxy while owner PID `90737` (birth
  `1791978428.887375`) and SDK PID `90782` (birth `1791978429.754991`) remained
  alive. That proves disconnect retention, not a clean old-proxy exit code or
  an EOF cleanup summary. These are actual client checks with a controlled SDK
  harness, not a native vendor-model turn. Evidence root:
  `/tmp/kacp-zed-preview-yyb8lco4/evidence`; owned trace
  `1791978427816980000-90605-*`, attached trace
  `1791978554330411000-2641-*`.
  The next connection sent `session/resume` and received `-32602`, reason
  `attached session is not owned by this connection`, because resume had been
  advertised unconditionally. Trace `1791978687875237000-16204-*` retains that
  rejection. `c7b6b740` now advertises only close for attached sessions, with
  load disabled; owned resume/load remain supported. Two direct regressions
  went from one failing/one passing to two passing; those and the existing
  real-stdio attached queued-cancel/EOF case passed together (three cases).
  This changes capability advertisement, not session ownership. Actual Preview
  trace `1791979136744185000-56715-*` now advertises `loadSession: false` and
  close only, sends no resume/load request, and opens attached session
  `ba44f79e99e6403aa68cac54fe7ea7b7`. Prompt
  `99d8d0f8-5206-4471-bf6d-120abedd7138` selects `vendor-deny` and returns
  `OWNED SDK DENIED`/`end_turn`, without `allow_always`.
  The actual owned shutdown gate failed: private Preview Quit removed its GUI,
  CLI, proxies and stdio, but owner `90737` and SDK `90782` remained alive under
  PID 1 after more than two minutes, with those same births. The CLI exited zero
  while reporting an app-quit timeout; it does not prove daemon cleanup.
  `root-owned-quit-postcheck.json` records the survivors at 12:08:39 UTC.
  Ownership repair `4d4ceff1` now opts owned ACP callers into
  `stop_on_owner_exit` and tracks daemon/engine parent lifetime. The lane ran
  59 focused cases, two actual parent-drop cases and ten startup cases; an
  independent rerun of five affected actual inbound-stdio cases passed in
  66.51 seconds. Counts overlap other runs. A fresh actual Preview run on
  `4d4ceff1` now passes normal approval/diff, actual partial UI Stop, attached
  denial without `allow_always`, and retained ownership when a borrowed client
  reloads/drops. One genuine Cmd-Q removed the GUI, crash helper, CLI, owned
  proxy/stdio, daemon, SDK and remaining attached proxy/stdio: all nine frozen
  PID/birth identities are gone. Original stable Zed PID `95659` retained its
  original birth. No post-quit GUI read, signal or cleanup RPC was used. The
  independent postcheck observed the owned processes gone at 13:10:21 UTC;
  later samples remain observations, not exact exit times. CLI exit zero
  coexists with its vendor app-quit-timeout log; missing proxy summaries do not
  prove graceful proxy exit codes. Source hashes were frozen before the new
  ACP run and stayed unchanged through quit. Evidence:
  `/tmp/kacp-zed-preview-yyb8lco4/evidence/root-ownership-rerun-postquit.json` and
  `/tmp/kacp-zed-preview-yyb8lco4/observer-owned-drop-success-frozen.json`.
  The frozen proof's appended `exact_private_socket_cleanup` confirms the
  registered owned socket and actual presence file are absent, using the
  private DNS PID/workspace/registration record and read-only stat. Its earlier
  incomplete socket-observation string remains historical; process exit alone
  was not treated as socket cleanup.
  This qualifies actual Zed Preview 1.24.2 with the controlled local SDK backend,
  not a native vendor-model turn. The malformed fixture, orphan failure and
  manual cleanup of earlier fixtures remain separate from this fresh shutdown
  proof. Existing stable Zed and user projects/settings were preserved.
- Native tool identity: `79e13257` assigns a new session-unique wire ID to each
  native invocation, even when an engine/provider reuses its input tool ID.
  Starts, completions and permission cards resolve through that invocation's
  mapping; repeated tool names are not identities. Repeated replay of unchanged
  saved history uses stable wire IDs in a separate scope from live invocations.
  Stored source IDs remain intact; history clearing or replacement has no
  durable ID promise. The 53-case run
  passed: 33 unit cases and 20 actual CLI/stdio cases in 173.81 seconds. The
  older 17/18-case checkpoints retain their own dates/coverage; counts overlap.
  These source/protocol checks do not supply the still-open successful native
  peer/model request below.
- Persistence: `d475fef2`, `168dfe07`, `008fecfa` and `25cdde92` repair native
  argument decoding, exact parent/tool-result matching, resumed base durability,
  typed ACP history producers and sparse card merges. Existing ACP logs lacking
  display metadata can recover it from a matching snapshot by exact UUID or
  unique shared parent UUID; original text/order/tail stay authoritative and old
  files are not rewritten. The replay regression run passed 150 tests plus five
  subtests; later typed history checks passed 20 tests. A copied version of the
  actual old Zircon data recovered five ACP assistant rows and eight cards with
  source bytes unchanged: `/tmp/kacp-olddata-recovery-4f0ez6ss/summary.json`.
  A separate copied native restore comparison at `8d8babd9` preserves all 64
  assistant texts, 65 cards/results, IDs, arguments, order and result metadata.
  All four logged raw user messages restore fully, including the complete
  1,000-character B2 request. Two error strings differ only by the native
  formatter's `Error: ` decoration; calling that actual formatter reproduces
  them exactly. Display metadata stays out of prepared model messages. No logger
  clipping or recovery of old injected context is justified. Evidence:
  `/tmp/kacp-native-parity-_mz9y98_/summary.json` and hash-only ledgers.
- Web UI: `decf3a83` serves the current source bundle; `d54ad450` clears the
  previous cancel header on admitted input; `95eaf915` omits unobserved durations
  instead of displaying 0 ms. `d39c99c3` uses the authoritative harness label for
  intentionally empty harness-managed model fields; actual Claude, Codex and
  native header titles were checked. Typecheck and build pass. Real controlled
  SDK/browser checks pass for startup errors, completed text/diff cards, pending
  permission reload and exact opaque approvals, Stop → reload partial history,
  and a subsequent normal turn. Cancelled cards retain the last vendor status.
  Actual 390px and 820px renders have no horizontal document overflow. Private
  screenshots and DOM evidence are under `/tmp/kacp-web-4xxf6eaj`, including
  `stopped-and-completed-390.jpg` and `stopped-and-completed-820.jpg`. This is
  controlled SDK UI evidence, not a Claude or Gemini model run.
- User runtime: the source engine/Web UI was restarted and the six engine-owned
  sessions were recreated with their previous profiles/identities and saved
  conversation bases. Unrelated external agents were preserved. Backups and the
  verified restore map are under `/tmp/kacp-live-restart-_swfzhr6`. Fresh Codex
  probe `ACP-UI-261014-A` called `hub_agents` and replied in Zircon's real window;
  it was an observation on a still-owned native reply chain, not a new peer
  request. The separate B probe failed at its native sender's rate limit before
  any Hub delivery. Direct CLI operator probe C passed: request/thread
  `0f403e9982d445538d8cd1a11f806ee3`, turn `7`, source `hub_attachment`, one
  parent-linked assistant with `end_turn`, 77 saved updates and a completed real
  roster tool card. Snapshot, HTTP history and JSONL display metadata agree.
  Metadata-only proof is in
  `/tmp/kacp-live-restart-_swfzhr6/live-qualification-summary.json`; the saved
  parent UUID matches the manager's user UUID. Logger-generated message/request
  IDs use a separate namespace and were not used as runtime request authority.
  Neither operator C nor observation A substitutes for the blocked native B gate.
  Retry B2 also terminated at the native provider rate limit before any Hub
  admission or Zircon record. Its observer compared the input and provider ID
  namespaces incorrectly and missed termination; an independent daemon query
  verified `provider_failed` and processing false. No resend was inferred from
  that timeout. Evidence: `/tmp/kacp-native-fresh-OoEaFR/verified-result.json`.
  Only the native sender was subsequently restarted on the new Hub source with
  its saved base restored; other user agents were preserved. Backup and restore
  evidence: `/tmp/kacp-native-restart-Sjsq0d`.
  A later isolated sender on `0a13b04d`, with no resumed conversation and one
  user message, sent exactly one 9,555-byte POST to the existing Z.AI
  Anthropic-compatible `/api/anthropic/v1/messages` endpoint at 04:13
  America/Phoenix on October 14. It returned HTTP 429, `rate_limit_error`, code
  `1308`; Z.AI's [error reference](https://docs.z.ai/api-reference/api-code)
  identifies a usage-limit reset window. The actual window/reset and
  authoritative token usage were not captured. The selected model alias does
  not establish the underlying model or a direct Anthropic service. Actual
  native operation `4546d2be96444733a14f3f058a1f70ad`, generation `1`, ended
  `provider_failed` with processing false. Its public controller input ID is a
  different namespace. There was no Hub send, network request/thread, receiver
  ACP admission or attributable Zircon final/end frame. The preexisting
  receiver's source revision was unattested. This fresh short context rules out
  restored oversized history as the sole cause, and does not qualify or
  vindicate B/B2. All owned processes, HTTP client, sockets and presence records
  were cleaned up; the user receiver and config were preserved. Evidence:
  `/tmp/kacp-fresh-native.KE50i8/summary.json`, `qualification-metadata.json`
  and `owned-process-audit.json`.
- Fresh terminal and browser checks: a real foreground Codex turn exposed ACP
  text being written into the footer without coordinator ownership. `917a434d`
  now previews per-request buffered text and prints one coordinated final
  response; `ad53626e` keeps adapter diagnostics in the file log while a TUI
  owns raw input, retaining bounded failure diagnostics and standalone/pipe
  stderr. Four projection regressions failed before the first repair; 45 focused
  native/display checks passed afterward. Controlled SDK PTYs also verified
  opaque approval, active Esc, partial history and visible refusal. The separate
  stderr repair has 21 projection/orchestrator checks plus four startup/restart
  and five refusal/exit checks; 28 actual assistant-stream reducer checks show
  no duplicated Web assistant or error. Counts overlap.
- Fresh actual-provider UI evidence at clean `ad53626e`: the real browser
  Session Options picker launched private `web-smoke` Claude and Codex sessions.
  Claude request `2979001630884d8ab005b86238394a45` passed browser approval and
  denial, two completed/failed saved cards, one parent-linked `end_turn` answer,
  an exact `approved\n` sentinel and no denied sentinel. Codex's first read-only
  turn attempted shell writes without requesting escalation; native shell
  redirections failed and no ACP permission/card was emitted. That failed
  permission probe remains recorded. A distinct explicit-escalation turn
  `b4a0b67559534afe84128809fc1c4bfb` then passed browser approval and genuine
  attached-terminal denial, with two saved completed/failed cards and a
  `cancelled` parent. The vendor's denial stopped that turn; no final-answer
  marker is inferred. Raw outgoing permission option IDs were not recorded.
  A real typed terminal reply passed afterward. All four Codex turns match their
  durable snapshots by exact request/update IDs, retaining the exact typed
  answer, tool status and cancelled partial text. Active terminal Esc on request
  `f45b9f1dac244ec39451e9a81e8962fc`, turn 4, followed six observed text chunks;
  actual `session/cancel` and `cancelled` completion arrived 36 ms and 44 ms
  after the key. This qualifies active inference cancellation, not a running
  shell-worker cancellation. Evidence: `/tmp/kwui-wu3zplht`, including
  `positive-permission-flow-summary.json`, `root-codex-esc-result.json` and
  `browser/`; attached PTY evidence is in `/private/tmp/kacp-attached-tui-6y2nux7q`.
  The earlier foreground Claude auth rejection remains separate; the fresh
  browser's current private copy worked without login or credential changes.
  Controller limits: the shared denial took 197 seconds instead of the intended
  90-second turn bound; a duplicate operator key left an unsent draft which was
  cleared before the next input. No extra model admission occurred.
- These fresh flows exposed two Web defects: Stop disappeared when the HTTP
  transport ended at a pending permission, and long vendor choice labels
  exceeded their cards. `d048763e` wraps those choices and their generic dynamic
  option sibling. `2b81ebf6` adds required-body `/cancel-owned`, forwarding only
  the exact parent request ID to the existing daemon owner/generation fence;
  `f128b4d0` binds the rendered owner, rechecks current and daemon prompts, and
  retains Stop while permission is pending. Permissions without a parent owner
  expose no new Stop control; generic native approval/denial and streaming Cancel
  retain their existing behavior. An old running engine returns 404 instead of
  silently using unscoped cancellation. Its real controlled browser test left
  the prompt and both turns intact with zero SDK cancel. A separate fresh
  engine/browser test stopped restored owner `4968a504a4954075a0b98598e046fa45`:
  one actual SDK `session/cancel` and `cancelled` completion, 34 saved text
  characters and the original `in_progress` card, no fabricated tool completion.
  Reload retained that partial; follow-up `32f514a7eb2d45b58bb55ad0927be9ae`
  approved the exact opaque choice, completed normally and survived reload.
  Pending count was zero and processing false. Actual CSS viewports 390 and 820
  had no document overflow; the long choice fit within its card in both. The
  820 screenshot API failed once; geometry was saved, and no missing image is
  claimed. Evidence: `/private/tmp/kpc-9fboht4i` and
  `/private/tmp/kpco-m5slc_ys`, including `frontend-final-checkpoint.json`,
  `actual-owner-stop-observation.json` and root browser captures. This is
  controlled installed-SDK proof, not a real-provider replay. Frontend 49 tests,
  typecheck and build pass; 48 engine/client-turn checks cover invalid bodies,
  exact and stale owners, replacement during RPC, native broker/no-body paths,
  malformed RPC results and a held cancellation lock. Counts overlap. The
  existing daemon boolean can acknowledge an initially matched owner even when
  its later generation check safely does nothing; physical cancellation is
  proved separately by SDK frames and saved completion. Actual-provider
  replay followed in the private run recorded below. Both
  controlled process families and owned sockets were independently checked gone;
  no provider auth copies or real model calls were used in those two fixtures.
- Fresh actual-provider replay on committed `e99fe9d6`: private fixture
  `/private/tmp/kpstop-esmwte_n` used the pinned installed Claude/Codex adapters,
  a 600-second permit and exactly three reviewed prompts per provider. Root
  alone operated the actual browser and borrowed interactive Claude terminal;
  observer lanes sent no prompts, choices or keys. Codex parent
  `4911dea964a8493d8738f608349b06ed` reached genuine permission, survived reload
  with Stop available and received one owner-scoped cancellation. SDK permission
  RPC 0 returned `cancelled`; the parent finished `cancelled`, retained 22 text
  characters and the vendor's actual `failed` card. No stopped sentinel was
  created. Parent `84e34a0efdad4847b3691f3e3ade4608` selected offered `allow_once`
  then offered `cancel` (`reject_once`) on separate real permissions: completed
  and failed cards, exact eight-byte `approved` sentinel and no denied file.
  Codex ended that denial turn `cancelled` with no final text; no reply marker
  is inferred. Recovery `097bd33ada894a93b80e3a8a7b8cd456` then ended normally
  with exactly `CODEX WEB RECOVERY OK`; that answer and earlier partial survived
  browser reload. Evidence: `codex-three-turn-observation.json`,
  `protocol.jsonl`, root DOM captures and `codex-final-reloaded.jpg`.
- The same actual run stopped Claude parent `f58df51ed92e42f3a09c505d99451625`
  after real permission and browser reload/reselection: one SDK cancel,
  `cancelled` permission outcome and parent, 23 retained text characters and
  unchanged vendor `pending` card. The Web UI displayed the cancelled parent
  separately from that last reported tool status; no stopped file existed.
  Actual terminal input admitted `93a05bb303da4a1a863ad13864f8b57a`; the offered
  `a` and `d` keys selected `allow-once` and `reject` on the respective commands.
  Only the approved eight-byte file existed; saved cards were completed/failed.
  The normal 181-character reply ended with `TUI ACP CLAUDE INPUT OK` and
  rendered above the Ready footer. Third parent
  `be887c34dd9148d0be27eb0390d7581f` was still streaming positive text when root
  pressed one Escape. SDK cancel followed 31.547 ms later, and the parent
  finished cancelled 44.763 ms after the key. The 6,098-character partial,
  with no tool or permission frames, persisted and survived browser reload;
  the terminal returned to Ready. Evidence:
  `/private/tmp/kctui-claude-rukp1c3n/evidence/claude-three-turns-final-observer-1792005824328435000.json`,
  its wire/terminal captures, `claude-tui-active-escape-key.json` and
  `claude-final-reloaded.txt` under the fixture root. Browser reload selected
  Codex automatically, so root explicitly reselected Claude; session-selection
  persistence is not claimed. The initial zero-model Codex approval-mode change
  returned `Failed to fetch`; one observed GUI retry returned HTTP 200 and
  ConfirmAll. That initial transport cause remains untraced, with its negative
  evidence retained separately. No account/auth refusal was retried.
- That six-turn fixture closed normally at its unchanged permit expiry. Root's
  independent birth check found no remaining recorded private runtime or
  terminal processes; all 12 preexisting user process births survived. Copied
  auth and private daemon sockets were removed. Original file/keychain hashes
  matched this fixture's baseline, and both unrelated test-file hashes stayed
  unchanged. Earlier unattributed keychain drift remains unresolved. The
  borrowed terminal exited on daemon disconnect; its exact private tmux server
  closed normally. Its retained socket inode refuses connections with errno 61;
  no claim that every socket pathname vanished is made. Exact Claude JSONL and
  snapshot parent IDs, content hashes, stop reasons and tool projections match
  for all three turns. Evidence: `cleanup.json`,
  `root-six-turn-independent-check.json` and
  `root-independent-cleanup-check.json` under the fixture root, plus
  `claude-jsonl-snapshot-parity-1792005903667189000.json` and the terminal
  cleanup/socket postchecks under the attached terminal's `evidence/` directory.
- Fresh real-provider test cleanup: the private model window expired normally
  after four Codex and one Claude admissions; all 202 recorded owned process
  birth entries were independently checked gone, and daemon sockets and copied
  auth were removed. The borrowed terminal exited on disconnect and its private
  tmux server closed normally; its retained inactive socket inode refuses
  connections. Cleanup and persistence ledgers are under the attached PTY root.
  Original files and Claude keychain bytes matched this run's baseline, and all
  12 preexisting process births survived.
  This does not resolve the older, unattributed keychain drift. The long-path
  setup failure was a fixture preflight omission: its derived 124-byte socket
  path failed Darwin bind before any SDK prompt; the corrected short-root run
  retained identical helper bytes. No user runtime was restarted for these
  source commits, and no push, release or installation occurred.
- New native → ACP feedback: a new private Koordinator/Codex Zircon pair on
  the recorded clean `d6b2d3cc` runtime boundary passes, using actual pinned
  Codex adapter 2.1.1/CLI 0.159.3 and the same configured Z.AI
  `/api/anthropic/v1/messages` route. The native sender's two actual requests
  returned HTTP 200. The model generated one ordinary new `hub_msg` message
  with `wait=true`, no `reply_to`, and request/thread
  `3bdb45551935461bb620280629d8043e`; after one scoped native approval, the
  receiver admitted it once as source `agent` and sent one ACP prompt. Its
  `hub_agents` and `hub_status` cards completed with successful saved outputs;
  two additional completed `Guardian Review` cards are internal thinking cards.
  The saved final is `ACP-NATIVE-PAIR-JUIV2I OK` with the original request,
  ACP session and `end_turn`. The sender received final
  `e29800db3bca451c89aba70ad93e08d8` and hidden end
  `6c3cb75b1f31435c9fdd15e6144f9604`, both on that original request/thread;
  the end reports one reply, no failure and `end_turn`. Actual native HTTP
  request 2 followed both frames, with the peer's final marker in post-initial
  context. This proves native feedback beyond socket receipt.
  The actual controller attempt took 47.047 seconds and remained red at
  `experiment.py:673`: its generic socket-tool observer missed `hub_status`,
  which `mcp_hub.py` serves directly from the authenticated binding. The saved
  real completed roster/status output supplies that evidence. The original
  helper, red `live-summary.json` and traces remain unchanged. A separate
  independent offline validator passes this same attempt with no additional
  model calls; it does not turn the original controller into a passing run.
  Both public processing predicates were false, queue sizes were zero, and
  turn locks, Hub owners, pending replies and waits settled. The receiver's
  `active_owner` diagnostic retained the completed request context; it was not
  an executing owner. Processing checks the actual RPC/future, queue,
  cancellation drain and completion tasks. The consumed native and ACP queues
  retain raw unfinished counters of `1`, because normal workers do not use
  `task_done`/`join`; these are not zero-work-owner claims. The sender's absent
  relay map was `None`, not zero, and neither private app joined a network.
  No new source failure or queue repair was established.
  Both apps exited zero and all 21 observed child PID/birth identities were
  gone. Auth copies, owned sockets and presence were removed; all nine original
  user process births and original files were preserved. No reopening test or
  internal Codex HTTP count was qualified here. Old user B/B2/restored histories
  and running UI sessions were not rerun or vindicated. The runtime source
  attestation was clean when this attempt ran; later unrelated test edits do
  not change that recorded boundary or imply the current checkout is clean.
  Evidence: `/tmp/kacp-native-pair-corrected.JUiv2I/root-verified-round-trip.json`,
  `verify_frozen_round_trip.py`, original `live-summary.json`, `manifest.json`
  and saved receiver snapshot in the same root.
  An earlier read-only prerequisite check at 13:36 UTC used the existing
  configured native profile's keychain entry for one quota GET, not a model
  call. It returned HTTP/business code 200 with current usage and next-reset
  fields. That later snapshot does not establish the missing reset time of the
  earlier `1308` failure. Actual capacity for this new round trip is proved by
  its two model HTTP 200 responses. Quota evidence:
  `/var/folders/m2/9jqrk_yj05nc0tw10f9nv0t80000gn/T/kacp-prerequisite-recheck-sb4kib4a/zai-quota-recheck.json`.
- Network: `99f4e9de` rechecks durable request authority after transport awaits
  and during remote ACP/CLI waits. Seven regressions reproduced hangs before
  the fix and pass afterward; 313 adjacent tests and two manual-grant/fast-final
  safeguards pass. An actual isolated two-host mutual-TLS run, 10.0.0.6 ↔
  10.0.0.7, passed correlated trust denial with zero model admission, in-flight
  revocation in 0.0044 seconds, accepted ask trust change in 0.2458 seconds and
  accepted CLI peer revocation in 0.2479 seconds. Owned probes exited and pending/
  dispatch maps were empty. Existing remote agents, installed source and real
  membership state were unchanged. Evidence:
  `/tmp/kacp-cross-device-wb6byW/qualification_summary.json`, `events.jsonl` and
  `source_sha256.json`. Its remote application handler is controlled; this does
  not qualify a remote ACP provider/harness.
- Real remote harness: a later isolated two-host run used an exact `9de09131`
  source archive, Python 3.14.7, SDK 0.12.1, MCP 1.30.0 and pinned Codex adapter
  2.1.1 (Codex 0.159.3) in a temporary remote runtime. Actual remote
  `hub_agents`/`hub_status` calls completed, 23 text updates and four tool cards
  rendered, and `ACP CROSS DEVICE CODEX OK` returned as one final message plus
  one hidden `end_turn` with the original request/thread. The complete run took
  41.256 seconds. Revoking only the isolated peer after model output began
  ended the next MCP wait in 0.2162 seconds with no accepted late return. Root
  independently reran the trace/correlation/hash validator. Credentials stayed
  on their original host; temporary auth copies were removed. Existing remote
  agents, source and the installed runtime were preserved; all owned processes
  exited, relay ports closed and pending/dispatch maps drained. Evidence:
  `/tmp/kacp-real-network-ILbHJu/qualification_summary.json`,
  `runtime_manifest.json` and `build_evidence.py`. This proves the isolated
  real transport/model boundary with explicit TLS and scoped membership;
  production discovery, enrollment, release and deployment are outside it.

Required open gates:

1. Gemini CLI 0.63.0: the empty-auth `-32000` and clean runner-close gate passes
   against `741e1a8d`; successful model/tool/permission/cancel runs still need
   supported authentication. Earlier session creation rejected the test
   account's consumer Google authentication and reports an Antigravity migration.
   Google retired this consumer path on June 18, 2026. The pinned CLI advertises
   API-key, Vertex and gateway alternatives; none is configured in the checked
   CLI environment/store. The model/tool/permission/cancel matrix needs an
   authorized supported mode. No account or billing settings were changed.
   Diagnosis: `/tmp/kacp-live-xVqw6o/gemini-qualification-diagnosis.md`;
   primary source: Google's
   [consumer deprecation notice](https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals).
   Today's read-only check at 13:34 UTC still found `oauth-personal` selected,
   existing consumer OAuth access/refresh fields, no API key in the checked
   Gemini stores/keychain, no supported API-key/Vertex environment or dotenv
   configuration, and no Kollab Gemini profile override. It inspected metadata
   only; no secret values were recorded and no account, login, billing or
   credential settings were changed. Evidence:
   `/var/folders/m2/9jqrk_yj05nc0tw10f9nv0t80000gn/T/kacp-prerequisite-recheck-sb4kib4a/auth-prerequisite.json`.
   The subsequent read-only Vertex prerequisite check refreshed the existing
   ADC token in memory and received HTTP 200 from Service Usage with
   `aiplatform.googleapis.com` **DISABLED**. BillingInfo returned HTTP 403
   `SERVICE_DISABLED`, so billing readiness is unknown. No prediction/model
   call, authentication-mode change, credential copy, API enablement or billing
   change occurred; protected credential/settings bytes stayed unchanged.
   Evidence: `/private/tmp/kacp-vertex-readiness-ty_dogwo/readiness.json`
   (`18360cbbbe3921200391eaf59e6c3d4524e3042f1947bd6f36cfadb5198ff55d`).
The real-provider Web UI and interactive terminal walkthrough gate is now
closed by the fresh private run recorded above, together with the earlier
actual browser and Codex terminal cases. Controlled SDK and narrow-layout
evidence remain separately identified. The earlier summary that only Gemini
remained preceded these actual user-facing acceptance checks and was too broad
at that time. The user's existing Web UI instance has now been refreshed onto
the updated engine, as recorded below; earlier private permission/Stop runs
remain separate from the fresh user-session reply checks.

User-runtime refresh on `307390ce` (2026-10-14):

- Engine `98484` owns loopback port 7433; the existing port-8080 server and
  external agents stayed running. All six engine session IDs, identities,
  profiles, agents and approval modes were preserved. Every restored session
  is ready and idle with zero pending permissions and the original three
  connected MCP servers / 43 tools. Early partial MCP observations settled
  after the native profile override's background tool initialization completed.
- Full live backups preserved 225 messages before shutdown. New canonical
  imports preserved their complete content, metadata, thinking and timestamps.
  Native initial restore arrays were exact; subsequent turns regenerate only
  the two dynamic Hub system blocks. All 223 original non-system messages and
  all 225 timestamps/metadata/thinking fields remain exact. Original stored
  conversation bytes remain prefixes after normal shutdown-save appends;
  existing snapshots were unchanged. The timestamp repair in `307390ce` passed
  22 focused resume tests.
- Fresh actual Web UI requests to Sapphire and Zircon returned exactly
  `CLAUDE_REFRESH_OK_261014` and `CODEX_REFRESH_OK_261014`, respectively, over
  HTTP 200. Each appears once as a user request and once as its correlated
  assistant reply. The first Claude submission never reached the daemon;
  browser reload and explicit send succeeded. The first Zircon selection hit a
  fetch error; a scoped retry after confirming healthy history/state GETs
  succeeded. These runs do not repeat or vindicate the earlier B/B2 tests.
- All 89 recorded old daemon/descendant births are gone. The old engine exited
  and holds no listener; its defunct PID remains until the untouched original
  UI parent reaps it. Only normal SIGTERM was used. The replacement engine
  inherited that launcher's initial environment; equality to the old engine's
  post-start environment was not verified. The engine token rotated normally.
- Codex vendor session IDs were retained. Claude vendor IDs rotated while
  profile/workspace/command/capabilities stayed exact; the exact vendor resume
  outcome/reason is unverified. No force, release, install, push, credential
  setting change or unrelated checkout edit is claimed.

Final getter proof:
`/private/tmp/kacp-refresh-observer-8tv729vt/capture-1792011999843170000/final-restoration-proof.json`
(`606d25760702e422a73f05eda0c33b6bc14f0a4838f6c4280bf05d0593bd97e5`).
Root physical/original-byte proofs and both Web UI screenshots/HTTP observations
are under `/private/tmp/kacp-user-runtime-refresh-bjegubpe/`. Gemini's
supported-auth positive rows remain open.

Counts above overlap and are not a combined distinct-test total. Private
artifacts and the owned Zed test folders are retained until the qualification
evidence is reviewed and the remaining model gates are resolved; cleanup then
requires explicit scoped authorization. No push, release, installation
of a Kollab release binary or production deployment occurred. The current
qualification goal is not complete.

## Audited repair goal after 9a123150

The source audit is complete for this repair scope. It compared the canonical
spec's current sections 3-8 with the outbound runner/orchestrator, native client
broker, inbound server, Hub/MCP/local and relay returns, permission bridge,
conversation state, engine projections and terminal/Web UI consumers. It also
inspected the installed SDK 0.12.1 schema, connection and router implementation.
These are source findings, not reproduced runtime results. Section 9's real
qualification and section 10's future features are separate from this goal.

The goal is to implement the following 13 concrete repairs, with small scoped
commits and source evidence for each. All items are open at goal creation.

1. **R1: Valid inbound native tool notifications.**
   `kollabor/acp_server.py:_tool_start/_tool_result` return `ToolCall` and
   `ToolCallUpdate`, without the notification discriminator. SDK
   `AgentSideConnection.session_update` requires `ToolCallStart` and
   `ToolCallProgress`. Emit those notification variants with `sessionUpdate`
   for both live native tools and saved native replay. Retain `ToolCallUpdate`
   for permission request cards, where it is the correct SDK model.
2. **R2: Reachable advertised inbound resume/close routes.**
   `KollabACPAgent.initialize` advertises resume/close, but `main` uses
   `acp.run_agent` with its default unstable routes disabled. SDK 0.12.1 marks
   both routes unstable and refuses them with method-not-found. Enable the
   required routes so capability declarations and dispatch agree; preserve
   attached-session restrictions and unknown-method errors.
3. **R3: Prompt cancellation releases its own queue slot and settles once.**
   `ClientTurnBroker.cancel` marks queued items but waits behind the active
   client before publishing their terminal event. `ACPRunner.cancel_queued`
   cancels the future but leaves the item occupying the ten-slot FIFO. Remove
   and settle queued requests promptly in both paths. Fence active native
   cancellation so the broker's immediate terminal and the later native
   completion cannot count as two terminals. Preserve FIFO and other owners.
4. **R4: Resume bindings follow the continued conversation ID.**
   `LocalStateService.resume_conversation` restores ACP before allocating the
   continued conversation's new native ID; `ACPOrchestrator.start` captures
   the earlier ID. Load from the original binding, then persist subsequent ACP
   bindings under the continued conversation ID. Keep profile/cwd/command
   matching and fresh-context fallback intact.
5. **R5: Persistence cannot strand admissions or skip owned cleanup.**
   `ACPOrchestrator.admit` queues before recording the user, and registers
   completion only afterward. `_complete` persists outside its error handler;
   `close` marks closed and saves before runner teardown. Inbound
   `new_session/close_session` also save before owned cleanup is guaranteed.
   Ensure every admitted request retains a completion owner and a correlated
   terminal failure even if history/binding writes fail. Teardown must run
   despite those errors, preserving the underlying error and attached daemons.
6. **R6: Exactly-once local and relay return consumption.**
   `HubPlugin._on_message_received` consumes ask replies before message-ID
   deduplication. Relay `_rpc_event` consumes verified returns before checking
   `presented`; verified-return checks already reject ordinary presented
   replays, but sibling/leased consumption needs an idempotence guard when
   presentation writes fail. Local redelivery can append an interim reply
   repeatedly.
   Deduplicate before mutable wait/receipt handling in both paths, after
   authorization. Preserve distinct reply/end events, ordered `earlier`, and
   normal treatment of legitimate late replies.
7. **R7: Question-back ownership and bounded continuation lifetime.**
   `ACPHubTools.questions` stores the old wait but no immutable question ID;
   answers use the latest peer ID instead. A subsequent peer message can change
   the reply target. Relay `_rpc_event` can consume a question without
   `observe`, leaving a new question thread unowned. `stop` retains question
   state, and question entries are not bounded. Store the exact question's
   ID/thread/peer and original wait; register verified remote receipts before
   returning their handles. Bound and expire continuation state, clear it on
   session retirement, and send question-answer end frames as hidden terminal
   actions. Both `hub_msg` and `hub_ask` must answer the correct question;
   `hub_ask` must then wait for the original request's final answer.
8. **R8: Final-answer fallback survives consecutive tools.**
   `ACPRunner._select_answer` searches only between the last two tool starts
   when no text follows the last tool. A stream `text, tool A, tool B` produces
   an empty answer although text exists before B. Select the last nonempty
   assistant text segment before the final tool when no later text exists;
   retain message-ID selection and exclude thoughts, tool output and interim
   Hub messages.
9. **R9: Owned process cleanup survives parent exit and late child birth.**
   `ACPRunner._terminate_process` snapshots descendants only while the adapter
   is alive, before SDK/session close. Already orphaned `setsid` children and
   children created during close escape that snapshot. Retain descendant
   ownership while the adapter lives, refresh it through teardown, and use
   process identity checks so reused PIDs are never signalled. Apply the spec's
   bounded five-second graceful/termination stages and reap owned survivors on
   clear, close, cancel timeout, startup failure and crash/restart.
10. **R10: Permission cancellation and resolution retain turn ownership.**
    `_acp_permission_decider` can return `None` on callback error/cancellation;
    `ACPPermissionBridge` can do so on outer timeout. `_request_permission`
    returns a cancelled outcome without universally arranging session cancel.
    Also `resolution_event` drops explicit request/turn fields from details,
    while SDK callbacks run outside a native turn context. Stop the matching
    generation when a valid selection cannot be made and preserve explicit
    ownership on resolution events so other ACP clients/windows close their
    prompts. Keep opaque options, exact grants and first-answer-wins behavior.
11. **R11: Merge live and restored harness tool cards in the Web UI.**
    `ACPOrchestrator` produces merged `tool_call_update/acp_update`, but engine
    `assistant_transport` handles only tool start and terminal result. It drops
    intermediate title/kind/status/content updates; terminal structured content
    is rendered as generic JSON. Carry the merged card by tool ID through the
    live transport and history projector, displaying text/diff output and
    current status without executing harness tools or duplicating cards.
12. **R12: Adopted/CLI-switched harness identity stays current.**
    `EngineSession._read_agent_facts` reads a profile once, `_track_events` uses
    `acp_session` only to clear state, and `list_sessions` refreshes names but
    not registered sessions' provider/harness. Switching profiles from an
    attached CLI can leave the Web UI's harness label/model controls on the
    prior provider. Update authoritative profile/harness state on changes,
    including transitions back to native, without persisting client modes.
13. **R13: Roster merging preserves circular-ask detection.**
    `HubPlugin._acp_roster` appends `_roster` summaries after full presence rows
    and keeps the last row per identity. `PresenceManager.get_roster_summary`
    omits tags, erasing peers' `acp_ask_waiting`, harness and approval-waiting
    metadata. Merge summaries without discarding current presence tags so
    asking an agent waiting on another ask returns `busy`, while the original
    requester remains eligible for a question back.

Execution ownership: outbound bridge/process repairs; Hub/relay/continuation
repairs; inbound protocol/broker and engine/Web UI projection repairs; root
conversation/persistence/permission integration and documentation. Only the
root stages and commits, one coherent repair at a time, with explicit paths.
All agents use the shared main checkout and preserve concurrent work.

Completion of this goal means the 13 source repairs are implemented, committed
and individually recorded below with source evidence. It does not qualify ACP
for production. By Marco's instruction, do not add/run tests, builds, lint,
adapter/client runs or live qualification. Those checks remain deferred;
never reuse the earlier checkpoint's results as proof of this repair pass.

### Repair commit ledger

Source inspection and scoped diffs are the evidence for this ledger. The
13-item source-repair goal is complete; runtime behavior remains unverified.

- R1 source fixed: `ed283dd2`; native live/replay share discriminated SDK helpers.
- R2 source fixed: `92988ec2`; inbound runner enables the required SDK routes.
- R3 source fixed: `ae020bd9`, `cae657a8`, `73baf934`; both queues release
  cancelled slots, native terminals share a private completion fence, and a
  cancelled queued client cannot finish another Web stream or inflate turns.
- R4 source fixed: `1248596b`; ACP restore follows native conversation renaming.
- R5 source fixed: `550e2240`, `947801d3`, `9c2d3aae`, `3409732d`, `f30f9cee`;
  admission/completion and inbound startup/save/close retain cleanup owners,
  partial attachments clean up, failed pool stops retain retry ownership, and
  engine clients receive a correlated visible error before failed completion.
- R6 source fixed: `e0064a22`; local and relay consumption deduplicate by sender,
  ID and action, preserving synthesized reply/end pairs.
- R7 source fixed: `7cc20b53`; immutable question receipts, original-final
  buffering, bounded continuation lifetime and hidden answer end frames.
- R8 source fixed: `c1b38e2f`; fallback scans earlier nonempty text segments.
- R9 source fixed: `e69262da`, `4c457ee3`; retained PID/birth identities, descendant
  refresh during shutdown, bounded grace/termination and explicit survivor
  errors; failed teardown owners remain reachable for retry. Capture of a child
  born, detached and orphaned wholly between polling observations remains
  unverified; this is not proof of complete
  live process containment.
- R10 source fixed: `a85df02f`, `41f28c25`, `d6f02499`, `d27c07ac`;
  explicit resolution ownership, cancellation settlement in the Web transport,
  waiting state cleared at the owned turn boundary, and
  tracked generation-scoped cancellation when no offered selection is valid.
  Timeout still chooses the first offered one-shot rejection when available.
- R11 source fixed: `8f0e414b`, `d27c07ac`, `ed4d64a1`, `84f206c1`; full merged
  cards survive history, live cards update by ID, text/diff content is rendered,
  and terminal parents stop activity without inventing a tool outcome.
- R12 source fixed: `0cfa0293`; authoritative profile events refresh engine
  snapshots, and initial adoption reads cannot overwrite newer profile state.
- R13 source fixed: `4093cf10`; fresh presence overrides cached summaries,
  preserving harness/waiting tags and circular-ask detection.

Source completion does not close the qualification gates: native cancellation
and permission races, process teardown (including detached children), nested
local/remote asks, persistence failure behavior, SDK/client interoperability,
and rendered Web/TUI behavior at 390px/820px have not been exercised. No version
was released or deployed and no commits were pushed. The shared main checkout
was preserved throughout; only explicit repair paths were staged.

## Source inventory

Current source inventory:

- `kollabor/llm/acp_bridge.py`: protocol negotiation, startup deadlines,
  serialized turns, structured permissions, steering, resume/load and process
  ownership including children that leave the process group.
- `kollabor/llm/acp_orchestrator.py`: daemon/profile ownership, transcript and
  tool-card records, correlated completion, batched observations, plans,
  context usage, permission waiting state and private session bindings.
- `kollabor/llm/acp_state.py`: device-local outbound bindings and stable
  inbound ACP-to-Kollab conversation mappings.
- `kollabor/acp_server.py` and `kollabor/acp_stdio.py`: ACP v1 stdio entrypoint,
  owned versus attached sessions, replay/load/resume, scoped prompt/cancel,
  permission callbacks and protocol-only stdout.
- `kollabor/llm/client_turns.py` and `kollabor_tui.turn_context`: native client
  FIFO admission, request ownership and correlation through daemon events.
- `kollabor/mcp_hub.py` and `plugins/hub/acp_tools.py`: authenticated
  `hub_status`, `hub_agents`, `hub_msg`, `hub_ask` and `hub_spawn` tools, owned
  threads, question-back continuation, bounded waits and circular-ask handling.
- Hub socket/routing/relay source: child-token validation, native bound asks,
  canonical terminal outcomes, typed ask/waiting markers, safe harness labels
  in directories and return delivery while an ACP prompt waits.
- Engine and Web UI source: harness identity, waiting badges, plan/context
  state, opaque permission choices, restored tool results, scoped grants and
  harness-owned model/effort controls. The CLI/status roster names harnesses.
- `FileOperationsExecutor` and `ToolExecutor`: bounded native file snapshots
  on edit/create/append/insert/delete/move/copy results, carried as ACP diffs.
- Native queue and state widgets: ACP usage events derived from native provider
  context limits and token counters, plus persistent harness context/cost state.

Review remains required for every changed surface. Real qualification remains
the acceptance bar in section 9 of the canonical spec; this source inventory
does not claim that any harness, ACP client, network run or UI run has passed.

## Review handoff

directive: implement the remaining ACP v1 source for the canonical spec, with
testing, builds, lint and live qualification deferred for later reviewers.
Read `bundles/skills/kollab-development/SKILL.md` before editing Kollab; network
review also requires its `references/relay-change-guide.md`.

execution: outbound lifecycle and bindings, Hub collaboration tools, network
returns, inbound stdio sessions, request correlation, file diff projection and
terminal/Web UI surfaces have implementation files in the shared checkout.
The source pass is recorded in Git and remains unqualified. Preserve unrelated edits and concurrent commits;
do not stash, reset, clean, switch branches or create a worktree here.

pivots: reuse SDK 0.12.1, the native daemon, native permission ownership and the
Hub/relay paths. ACP turns bypass native provider execution and HUD injection.
Inbound ACP submits into the daemon rather than introducing another model loop.

landmines: outbound turn generations and Hub child credentials are distinct;
process/session restart rotates the child binding. Native client turns inherit
correlation through `kollabor_tui.turn_context`; a request ID must remain on its
permission and terminal events. Attached EOF closes the connection only; owned
EOF stops its daemon. Relay fields `ask`, `waiting`, harness labels and terminal
outcomes require the current peer implementation; they confer no trust.
Local hidden terminal frames carry the hosting daemon's sender identity;
authenticated remote endpoints use their verified identity instead.

oversights corrected during this pass: ACP replies/broadcasts now enter the
observation batch before native HUD injection; steering does not acquire a
second final-answer owner; native stop strings such as `stop` and `length` map
to ACP outcomes; file snapshots are propagated into semantic tool metadata.

yield: the source pass is ready for independent review and qualification. The
following ownership boundaries need review; none has been executed as proof
during this pass:

1. Outbound runner/orchestrator/application: SDK calls, restart/clear/resume,
   FIFO ownership, steering, transcript ordering and final versus interim text.
2. Hub/MCP/socket/native bound asks: token/generation fencing, thread handles,
   question-back continuation, cancel/timeout, handoff and final/end settlement.
3. Relay/directory: authenticated source and target, room/trust/grant checks,
   request correlation and mid-turn delivery of answers and waiting notices.
4. Inbound server/stdio/client broker/state RPC: load/replay/resume, owned versus
   adopted daemons, client MCP isolation, mode persistence and scoped cancel.
5. Engine/TUI/Web UI/permissions: opaque options, reusable operation grants,
   prompt settlement across windows, harness labels, context/cost, plan state,
   restored cards and small-screen rendering.

Full diff cards are captured for UTF-8 text files up to 256 KiB. Larger or binary
files retain the tool result instead. Model selection/config options, multimedia
prompts, notices and ACP v2 remain the separate future scope in section 10.

Section 9 remains the acceptance gate: real Claude Code, Codex and Gemini
adapters; native/harness questions and circular asks; remote trust and replies;
an actual ACP client; cancellation/EOF; and real terminal/Web UI approvals.
Earlier fake/unit results below must not be reused as proof of this source pass.

## Earlier checkpoint

This document records the current implementation checkpoint; the canonical
phase contract remains in `docs/specs/acp-bridge.md`.

implemented in commits 22da746d, 854d3dbb, 557fdebe, fca1178f,
30ddd1b0, 02a7b848, 0f112962, a51d658d, ebbf1e2f, and e6951da0:

- portable `ACPProfile` command/cwd/workspace/env/pass_env resolution with
  provider-key stripping and device-local executable lookup
- built-in portable `claude-code`, `codex-cli`, and `gemini-cli` profiles with
  pinned install guidance for missing adapters
- device-local `~/.kollab/acp/resolved.json` cache recording command identity,
  symlink-preserving executable directory, and optional Node resolution;
  vanished or changed command paths are re-resolved, and adapter/Node
  directories prefix the child PATH
- pinned `agent-client-protocol==0.12.1` dependency and typed SDK connection
  for initialize, session/new, prompt, session updates, permission callback,
  cancel, process-group cleanup, and stderr draining
- FIFO prompt queue bounded at ten entries, process/session generation fencing,
  per-turn IDs, cancel rebasing for queued turns, typed final answer, and a
  correlated hidden turn-end frame
- fail-closed permission bridge using nested `AllowedOutcome`/`DeniedOutcome`,
  offered opaque `optionId` validation, timeout/cancel settlement, and
  allow_once/reject_once handling
- standard MCP SDK initialize/initialized/tools/list/tools/call server exposing
  `hub_status`; every call rechecks a parent AgentSocketServer binding over a
  temporary Unix socket and receives the current roster payload
- CLI flags `--acp-command`, `--acp-profile`, `--acp-prompt`, and hidden
  `--mcp-hub`; SDK fake, queue/cancel, permission, parent binding, revocation,
  and MCP ClientSession tests

validation checkpoint:

- 24 ACP bridge/profile tests pass, with focused ACP/MCP/CLI/input suites green
- `ruff`, `py_compile`, and `git diff --check` pass on changed files
- `kollab --version`, `kollab --hub status`, and live MCP ClientSession
  initialize/list/call plus parent roster-revocation smoke pass
- instrumented startup proof confirms ACP profiles skip native API/provider init
  (0/0 calls), while a native profile retains both calls (1/1)

remaining phase-1 gates at that earlier checkpoint (historical):

- canonical `--as`/`--llm` daemon/profile integration is not wired; current CLI
  flags are a one-shot harness path only
- real streaming/history/tool-card mapping, immutable origin/Hub final/end
  frame, lifecycle/steering/resume behavior, permission windows/grants/waiting
  line, and a real adapter run still need implementation/qualification
- installed `claude` and `codex` binaries report versions but have not been
  qualified as ACP adapters; no login/billing or fake proof substitutes this
- inbound ACP, hub message/ask, network routing, Web UI integration, reusable
  grants beyond the phase-1 option bridge, and broad profile support remain
  later phases in the canonical plan
