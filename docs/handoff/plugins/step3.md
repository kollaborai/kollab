# Plugins step 3a handoff

## Status

Step 3a is implemented on `feat/plugins-step3`, based on
`origin/feat/plugins-step2` at `82d1b390096aa43e73b3ca40a90fab581c1529ef`.
The loader, host, SDK, contribution surfaces, settings, install flow, Hello
plugin and test suites are on the branch. PR #1065 was open at last check; do
not open a step-3 PR while it is open or step 2 has no merged PR.

The lead's latest review `ui-review-580ab6fa2.md` found the wrapped Settings tab
row overlapping its panel at 820px. Browser regression commit `fafb6d927` fails
on the prior build (`review-overlap-red.log`); fix `e4541d0c2` removes
`md:h-full` from the tab triggers. Built-preview browser acceptance passes
(`review-overlap-green.log`). The review names `PanelHost.tsx`; no additional
Python change was necessary for this finding.

PhoneLink web parts are deferred to #1203. PhoneLink retains inventory and
read-only Settings and does not activate plugins. This behavior is unit-tested,
not checked on a paired phone. Other remote browsers are unaffected.

## Earlier evidence

At code head `580ab6fa2`, frontend typecheck, 463 Vitest tests, production build,
browser acceptance, and Python tests (350 passed, 1 skipped, including PhoneLink)
passed. Logs are `final-580-*.log` under `~/backups/plugins-step3-work/`.
The supervisor stack smoke and screenshot matrix passed at that code head;
`shots/580ab6fa2/` has 102 images and zero recorded overwide elements. The lead
reviewed those screenshots and found the overlap addressed above; they are not
visual sign-off for the new fix. No paired phone, relay, Docker or PhoneLink e2e
was used.

## Next

Run all frontend and Python gates and built browser acceptance at one fixed HEAD
using throwaway HOME and `KOLLAB_NO_KEYRING=1`. Request a supervisor stack
restart for that HEAD; repeat the live smoke and complete screenshot/overflow
matrix in `shots/<short HEAD>/`. Record exact commands, results, logs, HEAD and
folder here, then push `feat/plugins-step3`. Check PR #1065 before any PR action;
do not mark ready or merge. Lead reviews the new screenshots. Do not use image
comparison services, the owner stack, Docker, a paired phone, or a relay.
