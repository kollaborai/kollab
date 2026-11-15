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

Usage reads: `f865f00f` was red before `50ec8736` added GET usage, local-day
line attribution, partial-line handling, atomic counter/offset replay and
truncation detection. `765efc73` was red before `edd606bd` bounded ingestion
and offset checkpoints to 512 records. `ad66db21` clarifies the previously
unspecified runs.json container and terminal usage fields for step 4;
`a7529ca3` was red before `f2b40c78` reconciled completed run IDs exactly once.
`35a68612` was red before `6e022209` wired approved engine contexts to per-id
SSE, global notice events and heartbeat status. Actual dispatch and frontend
notice rendering are not yet wired. Eighteen focused tests passed
(`engine-run-usage-green.log`); Ruff and compilation passed. Logs are under
`<home>/backups/plugins-step2-work/`.

Dispatcher: `818e1efa`, `47ad19cb`, `325183e5`, and `ddc3a8d7` were red
before `57d4a6c5` added an approved per-plugin ASGI mount, development reload,
vendor-name conflict handling and cleanup, startup error reporting, and the
local-only guard. Twenty-three focused tests passed (`engine-dispatch-green.log`);
Ruff and compilation passed. Route tests now mock the supervisor so they never
start services. Earlier isolated-HOME route tests did not mock it; a later process
check found no matching test service, but cannot prove none was briefly started.
Earlier pytest invocations also used pytest's default temporary-directory policy;
from the dispatcher checkpoint onward tests use fresh, explicit backup-tree
basetemp paths to avoid pytest housekeeping against pre-existing temp data.

## Blocker and exact next step

Step 2 is not complete. Item 4 requires each background process to receive
`KOLLAB_ENGINE_URL` and its plugin-only `KOLLAB_PLUGIN_TOKEN`. The service wrapper
at `packages/kollabor-plugins/src/kollabor_plugins/services.py:251-264` constructs
a strict child environment that omits both, and the supervisor at lines 473-480
starts that wrapper independently. The fresh step 2 instructions expressly
expanded ownership for minimal service-child token/URL provisioning; `b04f296b`
implements it as described above. Multi-supervisor port changes remain unverified.

Token progress: tests `921d01c6`, `90ae0f15`, and `1a6918b2` were red before
`6d389eff` added per-plugin stable 0600 token files, scoped HTTP authorization,
router `ctx.token`, and plugin-WebSocket authentication. `42046844` specifies
the previously unnamed router token accessor. Twenty-six focused tests passed
(`plugin-token-green.log`); Ruff and compilation passed. These do not provision
the two environment variables to service children: that ownership gap remains.

Forwarded route policy: `83ebf610` was red before `41478c31` enforced remote
management denials in the engine and fixed the allowed agent-route shapes.
Package settings: `0f0ae1ce` was red before `b9ff77e7` added PUT through the
existing settings.save writer and published plugins-changed. Built-in writer
coverage `84b91902` was red before `2c15e57d`: settings.save and set_enabled now
handle built-ins, the terminal config panel routes them through the same writer,
and its local/global target selection is retained. ConfigService batches and
locks key writes; strict plugin writes refuse malformed files or conflicting
non-object sections. Focused config/panel/plugin/route regression: 310 passed,
1 skipped (`builtin-regression.log`); scoped Ruff and git diff --check passed.
These are unit checks, not a live UI or process smoke.

Service controls/logs: `a03a6d81` was red before `8513aabf` added start, stop,
async restart with shutdown cleanup, and bounded service-log reads with symlink
checks and secret/plugin/user-token redaction. Twenty-nine focused tests passed
with the known built-in settings test deselected (`plugin-service-routes-green.log`);
Ruff and compilation passed. All route tests mock service supervision/control.

Management progress after the previous checkpoint: `79ba8bc9` adds enable,
disable and remove guards; `6198e2b5` holds service locks during removal;
`e869b785` honors the log tail limit. Install/update staging, polling and
confirmation remain to be implemented and verified.

Service-child credentials: `737a5dcc` was red before `b04f296b`. The engine
and wrapper now use the same private token-file implementation; wrappers pass
a scoped token and validated loopback origin to children, redact token values,
and report credential preparation failures without exposing details. Engine
supervision and service actions pass their configured port to new wrappers.
Terminal-only supervision uses KOLLAB_ENGINE_URL or the default 7433. Existing
wrappers are intentionally not restarted merely because a later engine starts
on a different port: that multi-supervisor case still needs design/verification.
Plugin and engine regression: 257 passed, 1 skipped (`service-regression.log`);
scoped Ruff and git diff --check passed. No live engine/service smoke yet.

Next: remaining item 5 management routes, then PhoneLink filtering,
spoofing/normalization tests, all gates and smoke. The branch is not ready to
deploy: install/update jobs and PhoneLink management blocking are absent.
The lead resolved the PhoneLink scope mismatch: the ASGI bridge in
`packages/kollabor-engine/src/kollabor_engine/phone_link.py` is in scope only
for refusing the spec's remote-disallowed plugin routes and its tests;
`routes/phonelink.py` stays unchanged unless that refusal requires it. Audit the file deny list
against all actual token paths and tool entrypoints. Run the full unit suite on a
fixed HEAD, Ruff, compilation, and the specified isolated live smoke; record
results. Append the changelog line. Check PR #1065 only once step 2 is green;
follow the brief's conditional PR instructions. No PR, release, or deployment
has been created by this step. The base was step 1 commit `7ed6ec82`.

Earlier in this continuation, one diagnostic pytest invocation used `/tmp` for
its log and did not set an explicit backup-tree basetemp/HOME. No cleanup was
performed there; all subsequent test runs above used fresh backup-tree paths.
