code review protocol

when asked to review, audit, or check work — this is NOT a summary task.
your job is to FIND things, not restate what the spec already says.

review mindset:
  [1] findings first      bugs, risks, regressions, missing tests
  [2] severity ordered    critical > warning > nit
  [3] cite locations      file:line for every finding
  [4] be specific         "messenger.py:141 swallows the error silently" not "error handling could be better"
  [5] find what's NOT in the spec  stale files, silent failures, UX gaps, untested paths
  [6] keep summaries brief a 2-line overview after the findings, not before

what NOT to do in a review:
  [x] reformat the spec doc as your response
  [x] list what's done without checking if it actually works
  [x] say "looks good" without verifying with tools
  [x] summarize instead of audit

review checklist:
  [ ] stale backup/temp files left behind
  [ ] silent failures (caught exceptions with no logging/user feedback)
  [ ] untested code paths
  [ ] security gaps (missing auth, exposed secrets)
  [ ] UX gaps (error states with no actionable message)
  [ ] async/sync mismatches (sync function called from async without await)
  [ ] regression risks (does this break existing behavior?)

verify claims with tools. read the actual code. run the tests.
a review without tool calls is an opinion, not an audit.
