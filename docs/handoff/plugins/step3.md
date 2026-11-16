# Plugin web host: step 3a handoff

## Scope and current state

- Branch `feat/plugins-step3`, stacked on `origin/feat/plugins-step2` at `82d1b390`. The branch contains the web host work for #1028 and the cumulative implementation commits; see Git history for the authoritative changes.
- Lead decision: PhoneLink plugin web parts are deferred to #1203, including a separate design, security review, and red-first paired regression. Step 3a does not use a paired phone, relay, Docker, or PhoneLink e2e. Browser checks at 390px are ordinary browser checks, not PhoneLink. PhoneLink behavior is unit-tested, not checked on a paired phone.
- Canonical spec amended in `abcc7d4ff`. The phone uses an explicit capability to fetch inventory but never loads web parts; its settings are read-only and say `Open and manage plugins on this computer`. Other remote browser pages are unaffected. Red tests `ba0b9a87c` and `744ae204a` precede fixes `f3982641f` and `102c6f0d9` respectively. Focused tests: 34 passed; typecheck passed. Logs `phonelink-red.log`, `phonelink-green.log`, `phonelink-server-red.log`, `phonelink-server-green.log`, and `phonelink-server-typecheck.log` in the external backup work directory specified by the brief.
- Server error details are retained without mislabeling them as a web load failure. Desktop activation, startup, refresh, plugins-changed, Retry, list errors, renderer fallback and page opt-out are covered by the focused tests.

## Evidence and limitations

- Previous complete frontend and Python gates and real-chat smoke at `39e549dea` predate the PhoneLink changes. They are historical only; run every gate again at the frozen current HEAD. The prior captures show ordinary browser viewport behavior, not PhoneLink. Screenshot comparison services previously timed out; visual sign-off is not established.
- Asset hash verification has a remaining same-user filesystem race between the hash check and FileResponse open. Do not claim atomic snapshot containment.
- One earlier activation/settings regression was not separately committed red-first, and one earlier test launched a Vite dev server. History was not rewritten. Later fixes follow red-first commit discipline; run only built-bundle browser acceptance going forward.
- Existing logs and scripts live in the external backup work directory named in the brief. Keep logs and screenshots there, never in `/tmp`. Do not install dependencies. The scratch stack is supervisor-owned; never stop it or start Docker.

## Exact next step

1. Freeze this HEAD; append `RESTART-STACK` to the supervisor request file and wait for status to report this SHA. Reread the port. Do not start a separate stack. The previous restart request was made before subsequent commits and must not be assumed current.
2. With isolated HOME, no keyring and Node 24, run frontend typecheck, all Vitest tests, production build and built-bundle production-CSP browser acceptance. Run Ruff and py_compile for changed Python files, and the plugin and engine Python tests including `tests/unit/engine/test_phone_link_plugins.py`. Save logs externally; identify any failures. Do not run a dev server, relay or Docker.
3. At the same code HEAD, start a chat from the scratch web UI; run the real-chat smoke and stopped-state fixture using the existing external scripts, adapted for the supervisor port and new screenshot directory. Capture the specified matrix at 1440/820/390 in both themes, verify the live install/disable/failure cases, and count each viewport's overflow JSON. Close all browsers started. Prior captures are not evidence for this HEAD.
4. Update this handoff with exact HEAD, commands, results, limitations and next step; commit it. Push only `feat/plugins-step3`. Check PR #1065 as instructed in the brief; do not open a PR while #1065 is open or step 2 lacks a merged PR. Never merge, mark ready, or touch another branch.
