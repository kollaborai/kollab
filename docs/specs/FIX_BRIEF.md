# Fix brief

Add this section to the issue or PR body for a bug fix. The maintainer or fix
owner completes it; outside reporters can leave the optional issue-form field
blank.

## Fix brief
- Symptom: what fails and who sees it.
- Exact reproduction: the smallest steps and observed result.
- Root cause: the producer and `file:line`, not only the downstream symptom.
- Fix layer: where the behavior is corrected.
- Regression test: the red-first test that proves the failure and fix.
- Siblings swept: related paths or cases checked.
- Risk: what could still break and how it is bounded.
