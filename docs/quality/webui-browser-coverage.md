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

The runner executes all registered independent browser suites; locally it defaults to
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

The sidebar footer proof opens the mocked shell at 390px and 820px, checks all
eight controls are at least 44x44 CSS pixels, and checks that the footer and
page fit without horizontal overflow. At 1280px it checks that all eight
controls stay inside the desktop row without horizontal overflow at the default
sidebar width and at the minimum saved sidebar width. It saves
`sidebar-footer-touch-targets-390.png` and
`sidebar-footer-touch-targets-820.png` and
`sidebar-footer-touch-targets-1280.png` and
`sidebar-footer-touch-targets-1280-sidebar-220.png`. Run it from the frontend directory with
`node tests/sidebar-footer-touch-target-browser-acceptance.mjs <artifact-dir>`.

CI prepares the artifact directory before build and test commands and uploads
it with `if: always()`. A parallel run must not connect to a real user's
engine, use their home directory, or reuse a fixed port. The included suites
intercept application API requests or use synthetic fixtures; they do not
test a real remote agent or provider.

## Recording the chat frame by frame

The suites above play fixtures. `scripts/chat-recorder.mjs` drives the real
page against a running engine and a real agent, and records what the chat
shows on every animation frame. Use it before calling a change to the chat's
history sync, live turn or runtime done, and to compare a pull request's build
with `main`. It is not part of CI: each flow is a real model turn.

From `packages/kollabor-webui/frontend`, with a web UI and engine running and
a throwaway agent in a scratch folder (Trust All, so it can run `sleep`):

```sh
npm run record:chat -- all --agent <name> --web http://127.0.0.1:8080 --engine http://127.0.0.1:7433
npm run record:chat -- cut --agent <name> --out ../../../../chat-recordings
```

A change that touches the engine needs a side engine and web UI started from
that change's checkout on ports of their own: `python -m kollabor_engine serve
--port <n>` with `KOLLAB_CONFIG_DIR` naming a folder for its token, and the web
UI with `KOLLAB_ENGINE_URL`, `KOLLAB_WEBUI_PORT` and `KOLLAB_FRONTEND_DIST`
naming the built bundle. Pass them with `--engine`, `--web` and `--token-file`.

Each flow sends its own marked messages, so a bubble or reply shown twice is
counted exactly:

| Flow | What the page does |
|---|---|
| `plain` | One send and its reply |
| `tools` | One turn with three tool calls |
| `busy` | Sends again while its own turn runs |
| `cut`, `cuterr` | Its request's stream ends early (closed, or failed), then it sends again |
| `cutwatch` | Its request's stream ends early and it only watches from then on |
| `other` | Another client sends while its turn runs |
| `watch` | Only watches a turn another client started |
| `watchsend` | Sends while it watches another client's turn |
| `read` | Scrolls up while its own turn runs and ends |
| `three` | Three sends, each after the last settles |
| `reload` | Reloads in the middle of its own turn |

Per flow it writes, under `--out` (default `./chat-recordings`):

- `timeline.txt`: one clock for what the page did (`##`), what the wire carried
  (each request, state update, session event and history page), what the frames
  showed (`--`, with the last rows' words) and every defect (`!!`).
- `summary.json`: the defect counts. `video.webm` and `final.png`: the screen.
  `frames.json`: every sampled frame.

Defects: `BLANK` (rows went to none), `REMOUNT-ALL` (the whole thread was
rebuilt), `MASS-FADE` (three or more rows fading in at once), `DUP-USER`,
`DUP-REPLY` and `DUP-TOOLS` (a message, a reply or a turn's tool calls shown
twice), `REPLY-GONE` (a reply left the screen), `PICKER` (an "N / M" switcher),
`ANCHOR-MOVED` and `ANCHOR-LOST` (the row a reader scrolled to moved or was
rebuilt), and an error box still showing at the end. An error box that shows
and goes is logged, not counted: the cut flows drop a stream on purpose. The
command exits 1 when any flow shows a defect.

## Current evidence and gaps

- Learn, navigation, new-session shell and mocked remote selection: `learn-browser-acceptance.mjs`; the 11-topic tour, walkthrough video and unknown-topic route in `learn-topics-browser-acceptance.mjs`; the default-loadout scenarios (#957/#961) in `learn-loadouts-browser-acceptance.mjs`; release notes and notice lines (#887/#956) in `learn-notices-browser-acceptance.mjs`; the dismissed-retry and hand-made model changes (#959) in `learn-retries-browser-acceptance.mjs`. Still needed: real remote send/reply, switching, close/reopen, reconnect and history persistence.
- Profile rows: `acceptance/profile-rows.browser.mjs`. Still needed: full profile lifecycle and real authentication/error paths.
- Profile copy: `acceptance/profile-copy.browser.mjs`. Still needed: real keyring, provider and provisioned-profile negative cases.
- Agent sidebar glyphs and responsive layout: `agent-sidebar-glyph-browser-acceptance.mjs`. Still needed: agent launch, stop, deletion and remote failures.
- Sidebar footer touch targets and desktop fit: `sidebar-footer-touch-target-browser-acceptance.mjs`; checks 44x44px controls at 390px and 820px, and all eight controls fit the desktop row at 1280px.
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
