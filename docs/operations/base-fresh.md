# Base-fresh status

`ci/base-fresh` is a commit status that estimates whether changes merged into
`main` may have invalidated the tests relevant to an open pull request. The
workflow checks the trusted default-branch copy of the analysis script, posts a
status to the pull request head, and refreshes all open pull requests after a
push to `main`.

The script marks a pull request `STALE` under five rules:

1. `main` changes a global path such as a project or package manifest, lock
   file, Python version file, test configuration, workflow, action, or shared
   terminal test helper.
2. The same path changes on the pull request and `main`.
3. Changes on both sides can affect a shared test, or an unresolved import in
   test-covered pull request code may match a module changed on `main`.
4. Both sides change files under the web frontend.
5. Dynamic loading prevents a reliable comparison, or comparison data is
   incomplete.

This is an estimate. Two pull requests can still interact through configuration
or runtime paths that no test covers. The main-red bot remains the net for a
merge that leaves `main` checks red.

The status is shadow-only and is not a required check until a separate rule
change makes it required. Observe its results before changing that rule.
