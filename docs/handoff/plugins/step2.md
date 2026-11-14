# Plugin step 2 handoff

## Completed

Agent file operations deny reads of the global plugin directory, engine token
(including `KOLLAB_CONFIG_DIR`), and plugin-data run tokens. The check resolves
symlinks and runs before snapshots and handlers, including copy and grep. It is
not an OS sandbox and does not restrict shell tools. Test commit `f4c31235` was
red (17 failures); implementation commit `d3501a82` passed 25 targeted tests.
Logs: `<home>/backups/plugins-step2-work/read-deny-red.log` and
`read-deny-green.log`. Ruff and Python compilation passed for the two files.
Tests used the existing environment at `<home>/dev/kollab/.venv`,
with this worktree's package source directories prepended to `PYTHONPATH`, an
isolated HOME, and `KOLLAB_NO_KEYRING=1`; no packages were installed.

Web asset test commit `40474533` failed six checks first; implementation
commit `74ead5fe` passed all six. The route is before the SPA catch-all, limits
extensions and dotfiles, and checks containment; it sets nosniff. The web
boundary also sets `script-src 'self'`. Logs: `<home>/backups/plugins-step2-work/assets-red.log`
and `assets-green.log`. Ruff and compilation passed. Further coverage is needed
for dev/disabled/shadowed packages, root symlinks, traversals, CSP and the later
import-map hash integration; no full-suite or live smoke has been run.

Follow-up root-symlink test `51456285` was red and fix `65a3bccc` passed all
7 web asset tests. The engine now starts and cancels the existing service
supervisor with its lifespan: test `fb1bf7af` was red and implementation
`f45f117a` passed. Logs are `<home>/backups/plugins-step2-work/assets-symlink-{red,green}.log`
and `supervisor-{red,green}.log`. These are targeted checks, not a live smoke.

Engine `GET /plugins` now delegates to `plugin_entries()` and returns schema 1;
red test `6f438cd2`, implementation `ad5217b7`, two targeted tests passed.
Logs: `<home>/backups/plugins-step2-work/routes-list-{red,green}.log`.

A preliminary `PluginContext` is in `65b80775`; its hello-fixture test
`6259771f` was red before implementation and now passes. Logs:
`<home>/backups/plugins-step2-work/context-{red,green}.log`. This is not fully
integrated: event and notice publishers require engine callbacks, local-only
marking needs dispatch enforcement, and settings, secrets, locks and services
need complete contract and error-path tests. Do not treat the context as done.
The first lock-use test `a3d0c671` failed before fix `3b336d45`; both context
tests pass afterward (`<home>/backups/plugins-step2-work/context-lock-{red,green}.log`).

Remote WebUI management proxy restriction: red test `a647227f` produced four
failures; `ddb4e797` passed 12 targeted proxy/asset/engine checks. Logs:
`<home>/backups/plugins-step2-work/proxy-{red,green}.log`. The proxy strips any
client-supplied remote marker and sets its own for allowed remote plugin calls.
The engine does not yet enforce that marker for `ctx.local_only`, so remote
plugin API dispatch must not be considered safe until that is implemented.
PhoneLink enforcement is also outstanding. Add tests for allowed routes,
forwarded markers, normalization and spoofing.

Lock-directory symlink regression test `e36d325b` failed before fix `954044b5`;
all three context tests passed afterward. Logs: `<home>/backups/plugins-step2-work/
context-lock-symlink-{red,green}.log`. Ruff and compilation passed for the context
and test files. This rejects a pre-existing symlink; it is not an OS sandbox or
proof against concurrent directory replacement. Context remains incomplete.

Context contract coverage: `059a2cd5` was red on a corrupt values file before
`9bd4d6ae` normalized the error; `af7e6d3c` was red on undeclared stored
settings before `e71fa095` restricted returned values to declared settings.
The latter test also exercises hello settings save/defaults, grant denial,
event and notice callbacks, service status, lock and the step-4 agent stub;
`cb94744` adds unavailable-publisher and invalid-save coverage. Six context and
settings tests pass (`context-contract-green.log`); Ruff and compilation pass.
Logs are in `<home>/backups/plugins-step2-work/`. Context callbacks still need
to be wired to the engine event streams and actual NoticeBar delivery.

Engine reads so far: `810c5c79` was red before `382b1eab` added settings reads
for installed, development and built-in plugins; malformed values/manifests
return 409 and built-in secret/enable fields are omitted. `ed4c63d0`, `f9ae2040`
and `01884ef5` were red before `0d55d16a` added per-engine, bounded SSE queues,
global change streams, plugin event POST/GET, engine timestamps, heartbeat
validation and inventory heartbeat overlays, plus remote event allowance.
Fifteen focused tests passed (`engine-events-green.log`); Ruff and compilation
passed. These streams are not yet wired to management changes or PluginContext,
and the heartbeat overlay currently applies to engine inventory only.

Usage-log reads: `f865f00f` was red before `50ec8736` added GET usage, local-day
line attribution, partial-line handling, atomic counter/offset replay and
truncation detection. Six engine route tests passed (`engine-usage-green.log`);
Ruff and compilation passed. Counter metadata records per-source high-water
marks so a crash before the offset write does not duplicate a line. This does
not yet reconcile finished agent runs from runs.json; the step-4 run record's
container and finish-time field are not specified or implemented in this tree.

## Remaining and exact next step

Step 2 is not complete. Next: finish item 2: reconcile finished agent-run usage
without inventing a conflicting step-4 run schema; wire PluginContext events,
notices and service heartbeat status. Then implement the dispatcher, tokens,
proxy and PhoneLink remote restrictions, and all tests specified in the brief. The lead resolved the PhoneLink scope mismatch: the ASGI bridge in
`packages/kollabor-engine/src/kollabor_engine/phone_link.py` is in scope only
for refusing the spec's remote-disallowed plugin routes and its tests;
`routes/phonelink.py` stays unchanged unless that refusal requires it. Audit the file deny list
against all actual token paths and tool entrypoints. Run the full unit suite on a
fixed HEAD, Ruff, compilation, and the specified isolated live smoke; record
results. Append the changelog line. Check PR #1065 only once step 2 is green;
follow the brief's conditional PR instructions. No PR, release, or deployment
has been created by this step. The base was step 1 commit `7ed6ec82`.
