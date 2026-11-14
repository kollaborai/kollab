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

## Remaining and exact next step

Step 2 is not complete. Next: implement engine routes, PluginContext, tokens,
proxy and PhoneLink remote restrictions, supervisor tick, and all tests
specified in the brief. Audit the file deny list
against all actual token paths and tool entrypoints. Run the full unit suite on a
fixed HEAD, Ruff, compilation, and the specified isolated live smoke; record
results. Append the changelog line. Check PR #1065 only once step 2 is green;
follow the brief's conditional PR instructions. No PR, release, or deployment
has been created by this step. The base was step 1 commit `7ed6ec82`.
