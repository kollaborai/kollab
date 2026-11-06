# Changelog entries

A PR that changes what users see adds one file here. It does not edit `CHANGELOG.md`:

    changes/<issue>.<section>.md

- `<issue>` is the number of the GitHub issue the PR fixes, or a short slug when there is none. A second entry
  for the same issue adds `-2`, `-3` and so on: `835-2.fixed.md`.
- `<section>` is one of `added`, `changed`, `deprecated`, `removed`, `fixed` or `security`, the
  [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) headings.
- The file holds the entry as it will read in the changelog: a `- ` bullet in plain language that ends with the
  issue reference. A longer entry continues on lines indented by two spaces.

Example, `changes/835.security.md`:

    - Google API keys (`AIza...`) are masked in setup errors and logs (#835).

Two PRs never write the same file, so their entries cannot conflict.

`CHANGELOG.md` and its packaged copy `kollabor/updates/CHANGELOG.md` change only in release PRs (branch
`release/...`) and changelog repair PRs (branch `changelog/...`). `ci/standards-check` refuses a direct edit
anywhere else and checks every file here (`python scripts/changelog.py check`).

The release PR runs `python scripts/changelog.py build`. It puts each entry first under its heading in
`## [Unreleased]` of both copies, with one heading per section and the newest issue on top. It keeps the two
copies byte-identical and deletes the entry files. The release then moves `[Unreleased]` into the version
heading as before (`docs/release-process.md`).
