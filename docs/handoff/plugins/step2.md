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

## Remaining and exact next step

Step 2 is not complete. Next: wire and test PluginContext, then implement the
remaining engine routes, tokens, proxy and PhoneLink remote restrictions, and
all tests specified in the brief. The lead resolved the PhoneLink scope mismatch: the ASGI bridge in
`packages/kollabor-engine/src/kollabor_engine/phone_link.py` is in scope only
for refusing the spec's remote-disallowed plugin routes and its tests;
`routes/phonelink.py` stays unchanged unless that refusal requires it. Audit the file deny list
against all actual token paths and tool entrypoints. Run the full unit suite on a
fixed HEAD, Ruff, compilation, and the specified isolated live smoke; record
results. Append the changelog line. Check PR #1065 only once step 2 is green;
follow the brief's conditional PR instructions. No PR, release, or deployment
has been created by this step. The base was step 1 commit `7ed6ec82`.
