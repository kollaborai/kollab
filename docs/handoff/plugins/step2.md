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

## Remaining and exact next step

Step 2 is not complete. Next: implement the web asset route red-first, then
engine routes, PluginContext, tokens, proxy and PhoneLink remote restrictions,
supervisor tick, and all tests specified in the brief. Audit the file deny list
against all actual token paths and tool entrypoints. Run the full unit suite on a
fixed HEAD, Ruff, compilation, and the specified isolated live smoke; record
results. Append the changelog line. Check PR #1065 only once step 2 is green;
follow the brief's conditional PR instructions. No PR, release, or deployment
has been created by this step. The base was step 1 commit `7ed6ec82`.
