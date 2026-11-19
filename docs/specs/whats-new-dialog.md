# What's New dialog

Issue: #1106

Status: Implemented

Owner: Web UI

## Problem

After Kollab updates, the web UI does not automatically tell returning users what changed. The existing changelog page is reachable from the sidebar, but users can miss it.

## Goal and non-goals

Show a returning browser a short dialog once for each newer release when that release has user-facing Added or Changed entries. Keep the full changelog available through the existing page.

This feature does not replace the guided setup on a first run, announce an empty release, or show Fixed entries individually in the dialog.

## What people see

The dialog title is `What's New in <version>` and the subline is `Updated from <previous version>.` It lists Added and Changed entries, newest first, with at most six visible lines total across both sections. If there are more entries, it shows the remaining count. Fixed entries are summarized as `Plus <count> fixes.` when present.

`See All Changes` opens the existing changelog page and records the current version as seen. `Got It` and the header close button dismiss the dialog and record the current version as seen. Escape and outside-click dismissal also record it.

## Contract

- The release data comes from the packaged changelog parsed by the existing Web UI changelog model.
- The browser stores the last seen release version under localStorage key `kollab.whats-new.v1`.
- The decision model skips first runs, versions already seen, and releases with no Added or Changed entries. Otherwise it shows the dialog and supplies the previous and current versions and selected entries.
- `App.tsx` captures first-run eligibility on its initial render, before child effects or asynchronous startup can write setup progress. That snapshot owns the initial surface for the whole mount. Returning browsers evaluate the release after sessions load; stale startup work cannot open a dialog.
- Only complete stable `major.minor.patch` versions with canonical nonnegative safe-integer segments have an ordering. Seen records, release headings, previous-release labels, comparisons, and writes share the same parser. Unsupported strings cannot suppress announcements or appear as previous-release labels.
- The model produces capped, labelled sections and their overflow count together. The dialog renders those sections directly and does not split or select release arrays itself.
- `WhatsNewDialog.tsx` uses the existing SetupGuide dialog shell and routes `See All Changes` to the existing changelog page.

## Scenarios

- If a browser has a seen version older than the current release and the release has Added or Changed entries, then show the dialog once.
- If the current version is already recorded, then do not show the dialog again.
- If no setup progress has been recorded for this browser at initial render, then leave the first-run moment to guided setup, even if a child writes default progress while startup is pending. Returning browsers without a seen-release record can receive an announcement.
- If the release has no Added or Changed entries, then do not show the dialog.
- If the user chooses Got It, closes with X, presses Escape, or clicks outside, then record the current version and keep the dialog closed after reload.
- If the user chooses See All Changes, then open the existing changelog page and record the current version as seen.
- If the Added and Changed entries total more than six, then show six combined and indicate how many remain.

## Failure modes

- If localStorage is unavailable or cannot be written, do not crash the app; the browser may see the announcement again.
- If the packaged changelog has no announcement entries, do not show an empty dialog.
- If the stored seen record is missing, malformed, or unreadable, treat the browser as having no usable seen version.
- If changelog data is not ready or has no valid published release, do not show the dialog.
- If the release contains only Fixed entries, do not show the dialog.

## Decisions

- Added and Changed entries appear individually; Fixed entries appear as a count. This follows issue #1106 and the approved mockup.
- Seen state is per browser in localStorage, matching the guided setup record's browser-local behavior.
- The announcement is release-based: an empty Unreleased section does not suppress the newest published release.

## Test plan

- Unit tests cover newer-version show, same-version skip, first-run skip, empty-release skip, all 100 Added/Changed count splits from zero through nine per section, and strict version parsing including whitespace, unsafe numbers, and unsupported formats.
- Dialog tests cover rendered sections, fixes count, changelog navigation, and dismissal persistence callbacks.
- The built-App browser harness uses synthetic release/API data without starting a server or contacting a live service. It covers desktop, 820 px, and 390 px layouts; all five dismissal/navigation paths across reload; and pristine first run with and without a default-state write during startup. Independent visual review remains a separate gate.

## Docs and changelog fragment

- This specification: `docs/specs/whats-new-dialog.md`.
- User-visible change: `changes/1106.added.md`.

## Open questions

None.
