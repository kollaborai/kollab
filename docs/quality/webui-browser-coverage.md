# Web UI browser coverage

Issue #910 tracks a browser-level inventory and verification of the Web UI.
A static source inventory is a list of candidates, not evidence that controls
are reachable, usable, or tested. A passing browser suite proves only the paths
and mocked or live boundaries it actually exercises.

## Running the current browser suites

From `packages/kollabor-webui/frontend`:

```sh
npm ci
npx playwright-core install chromium
npm run build
npm run test:e2e:runner
npm run inventory:controls -- /tmp/kollab-control-candidates.json
npm run test:e2e:parallel
```

The runner executes ten independent browser suites; locally it defaults to
two workers, and CI fans the suites out across four parallel webui lane jobs
(core, learn, topics, notices) selected with `KOLLAB_BROWSER_SUITES` (#1022). Each has its own scratch home, temporary directory, browser context,
and dynamically allocated local listener. It does not inherit unrelated
environment variables. `KOLLAB_BROWSER_WORKERS` accepts 1–4
(CI sets it to 4); `KOLLAB_BROWSER_TIMEOUT_MS` bounds each suite (900 seconds
by default).
`CHROME_PATH` may select an existing Chromium executable. Results, captured
worker output, and suite screenshots go under a unique temporary directory or
under `KOLLAB_BROWSER_RESULTS_DIR` when specified. A failed or timed-out suite makes
the command fail. A timeout stops dispatch, requests graceful shutdown of all
active workers, and marks queued suites as `skipped`. On POSIX, the runner
checks that each owned process group has exited before reporting cleanup
complete. If a group remains after the grace period, results record
`unresolved-cleanup`, its PID/process-group ID, and the runner exits nonzero so
CI can upload the evidence. It does not force termination; that process tree
may still be running after the runner exits, so this is a visible fail-closed
limitation rather than an orphan-free guarantee. On Windows, the runner cannot
gracefully signal and verify a whole process tree with its current Node-only
mechanism. It reports cleanup as unverified and does not count that suite as
passed.

CI prepares the artifact directory before build and test commands and uploads
it with `if: always()`. A parallel run must not connect to a real user's
engine, use their home directory, or reuse a fixed port. The included suites
intercept application API requests or use synthetic fixtures; they do not
test a real remote agent or provider.

## Current evidence and gaps

- Learn, navigation, new-session shell and mocked remote selection: `learn-browser-acceptance.mjs`; the 11-topic tour, walkthrough video and unknown-topic route in `learn-topics-browser-acceptance.mjs`; the default-loadout scenarios (#957/#961) in `learn-loadouts-browser-acceptance.mjs`; release notes and notice lines (#887/#956) in `learn-notices-browser-acceptance.mjs`; the dismissed-retry and hand-made model changes (#959) in `learn-retries-browser-acceptance.mjs`. Still needed: real remote send/reply, switching, close/reopen, reconnect and history persistence.
- Profile rows: `acceptance/profile-rows.browser.mjs`. Still needed: full profile lifecycle and real authentication/error paths.
- Profile copy: `acceptance/profile-copy.browser.mjs`. Still needed: real keyring, provider and provisioned-profile negative cases.
- Agent sidebar glyphs and responsive layout: `agent-sidebar-glyph-browser-acceptance.mjs`. Still needed: agent launch, stop, deletion and remote failures.
- Team launch: `team-launch-browser-acceptance.mjs`. Still needed: live launch, permissions and failure/recovery.
- Chat: the remote lifecycle suite sends and receives through the UI, switches sessions, reloads and recreates a browser profile, and checks cached messages during synthetic history errors. A real remote persistence failure is tracked in #927. Still needed: live reconnection, busy state, attachments and additional errors.
- Settings and panels: partial profile flows above. Still needed: config, models, loadouts, MCP, permissions, session properties, versions and destructive confirmations.
- Hub and network: mocked remote selection only. Still needed: membership, invitations, trust, permissions, remote errors and recovery.
- Apps, prompts and Gem Studio: no end-to-end browser flow in this suite. Still needed: navigation, create/edit/remove actions, persistence and errors.
- Voice and PhoneLink: not in this parallel suite. Still needed: isolated relay/device flows, media permission failures and physical-device acceptance.

`inventory:controls` parses TSX source and emits candidate locations and labels
with `coverage: "unverified"`. Dynamic render branches, custom controls, runtime
permissions and hidden states require separate runtime discovery and human
classification. It cannot establish that every button has a meaningful test;
new source candidates must be mapped to workflows and real assertions before
claiming coverage. Do not automatically click every discovered control against
an existing installation: actions may send messages, alter settings or remove
data.
