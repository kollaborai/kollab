---
name: changelog-management
description: "Changelog Management skill - maintain version history and release notes"
---

changelog management mode: KEEP A CHANGELOG DISCIPLINE

when this skill is active, you follow Keep a Changelog standards.
this is a comprehensive guide to maintaining project changelogs.


PHASE 0: CHANGELOG CONTEXT DISCOVERY

before creating or updating ANY changelog, understand the project's versioning.


check for existing changelog

  <terminal>ls -la | grep -i change</terminal>
  <terminal>cat CHANGELOG.md 2>/dev/null || cat CHANGELOG 2>/dev/null || cat CHANGES.md 2>/dev/null || echo "no existing changelog"</terminal>

if changelog exists, analyze:
  - format being used (keep a changelog, custom, etc.)
  - version numbering scheme
  - categorization of changes
  - release date format
  - unreleased section handling


check version history

  <terminal>git tag --list | sort -V | tail -20</terminal>
  <terminal>git log --oneline --decorate | head -30</terminal>

identify:
  - current version
  - version tags in use
  - release frequency
  - version scheme (semver, calver, custom)


check project type and versioning

  <terminal>cat pyproject.toml 2>/dev/null | grep -A5 "version\|project"</terminal>
  <terminal>cat package.json 2>/dev/null | grep version</terminal>
  <terminal>cat Cargo.toml 2>/dev/null | grep version</terminal>

determine:
  - where version is stored
  - if using semantic versioning
  - if there's an automatic version bump system


check commit message patterns

  <terminal>git log --oneline | head -50</terminal>

analyze:
  - are commits following conventional commits?
  - is there a pattern (feat:, fix:, docs:, etc.)?
  - are issues referenced in commits?
  - are commit scopes used?


check for release tooling

  <terminal>cat .github/workflows/*.yml 2>/dev/null | grep -i release</terminal>
  <terminal>cat pyproject.toml 2>/dev/null | grep -A10 "commitizen\|release"</terminal>

identify:
  - automated release tools (release-please, semantic-release, etc.)
  - changelog generation tools
  - version bump automation


PHASE 1: UNDERSTANDING KEEP A CHANGELOG


the standard format

Keep a Changelog is the de facto standard for changelogs.
url: https://keepachangelog.com

core principles:

  [1] humans care about what changed, not when
      versions over dates as the primary organizer

  [2] categorize changes by type
      added, changed, deprecated, removed, fixed, security

  [3] list changes per version
      grouped by category under each version

  [4] link to actual commits
      provide traceability


changelog structure

  # Changelog

  All notable changes to this project will be documented in this file.

  The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
  and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

  [Unreleased]

  ## [1.0.0] - 2024-01-15

  ### Added
  - New feature that users will care about

  ### Changed
  - Something that worked differently now

  ### Deprecated
  - Feature that will be removed in future

  ### Removed
  - Feature that was deprecated and now removed

  ### Fixed
  - Bug fix for something

  ### Security
  - Security vulnerability fix


category definitions

  Added
    new features
    new capabilities
    new integrations
    new configuration options

  Changed
    behavior changes in existing features
    modified functionality
    updated defaults
    performance improvements

  Deprecated
    features marked for future removal
    features that will change incompatibly

  Removed
    features removed from the project
    deprecated features that reached end of life

  Fixed
    bug fixes
    regression fixes
    crash fixes

  Security
    vulnerability fixes
    security hardening
    dependency updates for security


PHASE 2: SEMANTIC VERSIONING


semver basics

semantic versioning: MAJOR.MINOR.PATCH

  MAJOR
    incompatible API changes
    examples:
      - remove a public function
      - change function signature
      - modify behavior contract

  MINOR
    backwards-compatible functionality
    examples:
      - add new public function
      - add new optional parameter
      - add new feature flag

  PATCH
    backwards-compatible bug fixes
    examples:
      - fix crash condition
      - fix incorrect output
      - fix edge case handling


version bumping rules

  [1.0.0] -> [1.0.1]
    patch release
    fixed a bug

  [1.0.1] -> [1.1.0]
    minor release
    added a feature

  [1.1.0] -> [2.0.0]
    major release
    breaking change

  pre-release versions:
    [1.0.0-alpha], [1.0.0-alpha.1], [1.0.0-beta], [1.0.0-rc.1]


development versions

during development, use pre-release identifiers:

  - alpha
    internal testing, not feature complete
    [1.0.0-alpha]

  - beta
    feature complete, public testing
    [1.0.0-beta]

  - rc (release candidate)
    testing for bugs, no new features
    [1.0.0-rc.1]

  ordering: alpha < beta < rc < final


PHASE 3: CONVENTIONAL COMMITS


commit message format

conventional commits work hand-in-hand with changelogs:

  <type>[optional scope]: <description>

  [optional body]

  [optional footer(s)]

types:
  feat:     new feature
  fix:      bug fix
  docs:     documentation only
  style:    formatting, no code change
  refactor: code change without feature/fix
  perf:     performance improvement
  test:     adding or updating tests
  chore:    maintenance tasks
  ci:       CI/CD changes
  build:    build system changes
  revert:   revert a previous commit


examples:

  feat: add plugin system
  feat(api): add streaming endpoint
  fix: correct memory leak in parser
  fix(auth): resolve token expiry issue
  docs: update installation guide
  chore: upgrade dependencies
  perf: reduce database query time
  ci: add github actions workflow


breaking changes

mark breaking changes in commit:

  feat!: redesign user interface

or with body:

  feat(api): remove deprecated endpoint

  BREAKING CHANGE: endpoint /v1/users no longer exists


scope usage

scope categorizes changes by module/area:

  feat(auth): add oauth2 support
  fix(database): resolve connection pool issue
  docs(readme): update quick start
  refactor(ui): extract component library

common scopes:
  - core, api, cli, ui
  - auth, database, storage
  - readme, contributing, guide


PHASE 4: CREATING A NEW CHANGELOG


initial changelog setup

  <create>
  <file>CHANGELOG.md</file>
  <content>
  # Changelog

  All notable changes to this project will be documented in this file.

  The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
  and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

  [Unreleased]

  ## [0.1.0] - YYYY-MM-DD

  ### Added
  - Initial release
  </content>
  </create>


migrating from no changelog

if you have existing releases without a changelog:

  [1] list all git tags
      <terminal>git tag --list | sort -V</terminal>

  [2] for each significant release, analyze commits:
      <terminal>git log --pretty=format:"%h %s" <prev-tag>..<tag></terminal>

  [3] create changelog entries retroactively
      focus on user-facing changes only

  [4] use approximate dates from tags:
      <terminal>git log -1 --format=%ai <tag></terminal>


migrating from custom format

if you have an existing changelog in non-standard format:

  [1] preserve the history
      don't delete existing entries

  [2] create a new section following keep a changelog
      start with [Unreleased]

  [3] gradually migrate old entries
      if time permits, reformat historical entries

  [4] note the format change
      add note explaining the switch


PHASE 5: THE UNRELEASED SECTION


purpose of unreleased

collect changes that haven't been released yet:

  [Unreleased]

  ### Added
  - New feature in progress

  ### Changed
  - Modified existing feature

  ### Fixed
  - Bug that was fixed but not released


working with unreleased

as you make changes, add them to [Unreleased]:

  commit: feat(api): add streaming endpoint

  immediately add to CHANGELOG.md:

  [Unreleased]

  ### Added
  - Streaming endpoint for real-time responses


releasing from unreleased

when releasing:

  [1] create version header
      move unreleased items to new version

  [2] add release date
      use ISO format (YYYY-MM-DD)

  [3] create new unreleased section
      empty placeholder for next release

  [4] create git tag
      <terminal>git tag -a v1.2.0 -m "Release v1.2.0"</terminal>


PHASE 6: WRITING EFFECTIVE CHANGE ENTRIES


entry writing guidelines

  [1] write for users, not developers
      explain the impact, not the implementation

  [2] be specific but concise
      what changed and why it matters

  [3] use active voice
      "Added feature" not "Feature was added"

  [4] group related changes
      list multiple related items as sub-bullets


good vs bad entries

  bad:
    - Fixed bug in parser

  good:
    - Fixed parser crash when handling nested comments

  bad:
    - Performance improvements

  good:
    - Reduced startup time by 40% through lazy loading

  bad:
    - Changed API

  good:
    - Changed authentication to require API key in header


migration entries

for breaking changes, provide migration path:

  ### Changed

  - Authentication header now requires Bearer token
    Migration: Update requests to include `Authorization: Bearer <token>`
    instead of `X-API-Key: <key>`

  - Database schema for users now requires email field
    Migration: Run `python migrations/add_user_email.py` before upgrading


scope prefixes

use scope prefixes for clarity:

  - (auth) Added OAuth2 support
  - (cli) New --verbose flag for debugging
  - (docs) Updated API reference

scopes help users find relevant changes quickly.


PHASE 7: LINKING CHANGELOG TO COMMITS


commit linking strategies

provide traceability from changelog to code:

  ### Added
  - New plugin system (abc1234)

or with issue:

  ### Added
  - New plugin system (#123)

or with both:

  ### Added
  - New plugin system (abc1234, closes #123)


automatic linking

github/gitlab auto-link patterns:

  #123 links to issue 123
  abc1234 links to commit abc1234
  @username links to user

use these in changelog entries:


  ### Fixed
  - Memory leak in connection pool (@devuser, #456)


link format in versions

at the bottom, add version links:

  [Unreleased]: https://github.com/user/repo/compare/v1.0.0...HEAD
  [1.0.0]: https://github.com/user/repo/compare/v0.9.0...v1.0.0
  [0.9.0]: https://github.com/user/repo/releases/tag/v0.9.0

allows clicking version to see diff.


PHASE 8: HANDLING DIFFERENT CHANGE TYPES


added entries

feature additions:

  ### Added
  - Plugin system for extensibility
  - Support for OpenAI and Anthropic APIs
  - Configuration file validation
  - (cli) --version flag to display current version
  - (docs) Plugin development guide


changed entries

behavior modifications:

  ### Changed
  - Default timeout increased from 30s to 60s
  - Error messages now include troubleshooting hints
  - (api) Response format includes metadata field
  - (ui) Status indicators use color by default


deprecated entries

features being phased out:

  ### Deprecated
  - Old API endpoint /v1/chat (use /v2/chat instead)
  - --legacy flag (will be removed in 2.0.0)
  - Python 3.10 support (migrate to 3.11+)


removed entries

deleted features:

  ### Removed
  - Support for Python 3.9 (end of life)
  - Deprecated REST API v1
  - Old configuration file format


fixed entries

bug fixes:

  ### Fixed
  - Crash when handling empty response from API
  - Memory leak in long-running sessions
  - (auth) Token refresh now happens correctly
  - (cli) Input not being saved in pipe mode


security entries

vulnerability fixes:

  ### Security
  - Updated dependency to fix CVE-2024-12345
  - API keys now excluded from debug logs
  - Added input validation for file operations


PHASE 9: RELEASE NOTES VS CHANGELOG


changelog vs release notes

changelog:
  - comprehensive record of all changes
  - developer-focused
  - updated continuously
  - includes all commit types

release notes:
  - highlights for end users
  - marketing-friendly
  - crafted for each release
  - focuses on features and fixes


generating release notes from changelog

for a GitHub release, extract:

  [1] headline features from Added
  [2] important fixes from Fixed
  [3] migration notes from Changed/Removed
  [4] security issues from Security

example release notes:

  What's New in v1.2.0

  Features:
  - Plugin system for extending functionality
  - Support for multiple LLM providers

  Fixes:
  - Fixed crash when handling empty responses
  - Resolved memory leak in long sessions

  Upgrade Notes:
  - If using the old REST API, migrate to v2 before upgrading
  - Update configuration files to new format

  Security:
  - Updated dependency for CVE-2024-12345


PHASE 10: AUTOMATED CHANGELOG TOOLS


commitizen / cz-cli

standardized commit messages with automatic changelog:

  <terminal>npm install -g commitizen cz-conventional-changelog</terminal>
  <terminal>echo '{ "path": "cz-conventional-changelog" }' > ~/.czrc</terminal>

use:
  <terminal>git cz</terminal>

prompts for:
  - type (feat, fix, docs, etc.)
  - scope
  - description
  - body
  - breaking changes


standard-version

automated versioning and changelog:

  <terminal>npm install -g standard-version</terminal>

run before release:
  <terminal>standard-version</terminal>

automatically:
  - bumps version in package.json
  - updates CHANGELOG.md from commits
  - commits the changes
  - tags the release


release-please

github action for automated releases:

  .github/workflows/release-please.yml:
    name: Release Please

    on:
      push:
        branches: [main]

    permissions:
      contents: write

    jobs:
      release-please:
        runs-on: ubuntu-latest
        steps:
          - uses: googleapis/release-please-action@v3

          with:
            release-type: python
            package-name: kollabor


semantic-release

fully automated releases:

  <terminal>npm install -D semantic-release</terminal>

  .releaserc.json:
    {
      "branches": ["main"],
      "plugins": [
        "@semantic-release/commit-analyzer",
        "@semantic-release/release-notes-generator",
        "@semantic-release/changelog",
        "@semantic-release/npm",
        "@semantic-release/git",
        "@semantic-release/github"
      ]
    }

analyzes commits to determine version bump automatically.


PHASE 11: CHANGELOG FOR DIFFERENT PROJECT TYPES


library changelog

focus on API changes:

  # Changelog

  ## [2.0.0] - 2024-01-15

  ### Added
  - async variants of all core functions
  - type hints for public API

  ### Changed
  - (api) parse() now returns Result object instead of raising
  - minimum Python version increased to 3.11

  ### Deprecated
  - parse() with raise_exception=False (use parse_safe())

  ### Removed
  - Python 3.10 support
  - legacy Config class (use ConfigBuilder)

  ### Fixed
  - type hints corrected for generic types


application changelog

focus on features and fixes:

  # Changelog

  ## [1.5.0] - 2024-01-15

  ### Added
  - Dark mode theme
  - Export conversations as markdown
  - Keyboard shortcuts for common actions

  ### Changed
  - Improved startup performance
  - Redesigned settings interface

  ### Fixed
  - Fixed crash when loading large conversations
  - Corrected scrolling behavior in chat view


cli tool changelog

focus on commands and options:

  # Changelog

  ## [2.0.0] - 2024-01-15

  ### Added
  - --watch flag for continuous monitoring
  - --format option for json output

  ### Changed
  - default verbosity level (use -v for more output)
  - --output flag now accepts directory paths

  ### Removed
  - --legacy flag (use --compat instead)

  ### Fixed
  - pipe mode now handles binary data correctly


PHASE 12: CHANGELOG IN THE RELEASE PROCESS


pre-release checklist

before cutting a release:

  [ ] review unreleased section
  [ ] categorize all uncategorized items
  [ ] verify all entries are user-facing
  [ ] add migration notes for breaking changes
  [ ] link to relevant issues/commits
  [ ] set version number based on semver
  [ ] add release date


release workflow

  [1] prepare the changelog
      create version section from unreleased

  [2] create release notes
      craft user-friendly summary

  [3] update version file
      <terminal># edit version in appropriate file</terminal>

  [4] commit the changelog
      <terminal>git add CHANGELOG.md</terminal>
      <terminal>git commit -m "chore: prepare release v1.2.0"</terminal>

  [5] create the tag
      <terminal>git tag -a v1.2.0 -m "Release v1.2.0"</terminal>

  [6] push tag to trigger release
      <terminal>git push origin v1.2.0</terminal>

  [7] create new unreleased section
      empty section for next cycle


post-release

  [ ] verify release notes rendered correctly
  [ ] check changelog links work
  [ ] announce release (if applicable)
  [ ] update documentation with new features
  [ ] close related issues


PHASE 13: HANDLING HOTFIXES


hotfix changelog entry

for urgent fixes:

  # Changelog

  [Unreleased]
  ### Fixed
  - Hotfix items here

  ## [1.2.1] - 2024-01-16

  ### Fixed
  - Critical security vulnerability in auth
  - Regression introduced in 1.2.0


hotfix process

  [1] create hotfix branch from release tag
      <terminal>git checkout -b hotfix/1.2.1 v1.2.0</terminal>

  [2] add fix and update changelog
      create version section for hotfix

  [3] commit, tag, release hotfix
      <terminal>git tag -a v1.2.1 -m "Hotfix v1.2.1"</terminal>

  [4] merge back to main and develop
      <terminal>git checkout main && git merge hotfix/1.2.1</terminal>

  [5] main unreleased should stay intact
      don't lose changes already tracked there


PHASE 14: CHANGELOG QUALITY CHECKLIST


before publishing a changelog:

  [ ] follows keep a changelog format
  [ ] versions in chronological order (newest top)
  [ ] each version has release date
  [ ] categories properly used (added, changed, etc.)
  [ ] entries are user-facing, not implementation details
  [ ] breaking changes include migration notes
  [ ] entries use active voice
  [ ] scope prefixes used where helpful
  [ ] commit/issue links provided
  [ ] version links at the bottom
  [ ] no typos or grammatical errors
  [ ] unreleased section exists (even if empty)


testing your changelog

  <terminal># verify markdown renders correctly</terminal>
  <terminal># check that all links work</terminal>
  <terminal># ensure version ordering is correct</terminal>
  <terminal># confirm dates are in ISO format</terminal>


PHASE 15: COMMON CHANGELOG ANTI-PATTERNS


anti-pattern: implementation details

problem: entries describe how, not what

  bad:
    - Refactored parser to use new library
    - Changed variable names in auth module

  good:
    - Improved parsing error messages
    - Fixed authentication edge case


anti-pattern: missing migration info

problem: breaking changes without guidance

  bad:
    ### Changed
    - Removed old API endpoint

  good:
    ### Changed
    - Removed deprecated /v1/chat endpoint
      Migration: Update to /v2/chat endpoint (see migration guide)


anti-pattern: vague entries

problem: non-specific descriptions

  bad:
    - Various improvements
    - Bug fixes
    - Performance optimizations

  good:
    - Reduced database query time by 40%
    - Fixed crash when handling unicode input
    - Added caching for API responses


anti-pattern: developer-only changes

problem: listing internal changes

  bad:
    - Added unit tests
    - Updated dependencies
    - Fixed linting errors

  (these should be in commit history, not changelog)

  good:
    (only include what affects users)


anti-pattern: wrong categorization

problem: misclassifying changes

  bad:
    - New feature listed under "Fixed"
    - Removal listed under "Changed"

  good:
    - New features under "Added"
    - Removals under "Removed"
    - Behavior modifications under "Changed"


PHASE 16: MAINTAINING CHANGELOG CONSISTENCY


entry formatting

consistent style within changelog:

  ### Added
  - Feature name
    Additional details if needed

  - Another feature
    Details spanning
    multiple lines

  - Scope: Feature with scope prefix


capitalization

  [ok] Added, Changed, Deprecated, Removed, Fixed, Security
  [ok] Each entry starts with capital letter
  [ok] Proper nouns and acronyms capitalized (API, URL, JSON)


punctuation

  [ok] entries don't end with periods
  [ok] complete sentences in details end with periods
  [ok] lists don't use trailing punctuation


version format

  ## [1.2.3] - 2024-01-15

  - version in brackets
  - space after bracket
  - dash before date
  - ISO date format
  - no time in date


PHASE 17: CHANGELOG FOR MONOREPOS


monorepo changelog strategies

for projects with multiple packages:

option 1: single changelog

  # Changelog

  ## [2.0.0] - 2024-01-15

  ### Added
  - (package-a) New feature in package-a
  - (package-b) New feature in package-b

  ### Changed
  - (core) Core library change affecting all packages


option 2: per-package changelogs

  package-a/CHANGELOG.md
  package-b/CHANGELOG.md
  CHANGELOG.md (root, for project-wide changes)


option 3: combined with sections

  # Changelog

  ## [2.0.0] - 2024-01-15

  ### package-a
  - New feature
  - Bug fix

  ### package-b
  - Different feature
  - Different fix


choosing the right approach

  small monorepos (2-3 packages)
    use single changelog with scope prefixes

  medium monorepos (3-10 packages)
    use per-package changelogs with root summary

  large monorepos (10+ packages)
    use independent per-package changelogs
    generate combined view automatically


PHASE 18: CHANGELOG ANALYSIS AND REPORTING


extracting insights

changelog data can inform decisions:

  release frequency
    analyze version dates to find release cadence

  change type distribution
    count categories to find project focus

  breaking changes
    track breaking changes to assess stability

  hotfix frequency
    many hotfixes indicates quality issues


example analysis

  <terminal># count changes by type in last year</terminal>
  <terminal>grep "^###" CHANGELOG.md | sort | uniq -c</terminal>

  <terminal># find release frequency</terminal>
  <terminal>grep "^## \[" CHANGELOG.md | head -20</terminal>


PHASE 19: CHANGELOG RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] ALWAYS use keep a changelog format
      categories: added, changed, deprecated, removed, fixed, security

  [2] write for USERS, not developers
      explain the impact of changes
      omit internal implementation details

  [3] include unreleased section
      place at top of changelog
      even if empty, keep the placeholder

  [4] use semantic versioning
      MAJOR.MINOR.PATCH
      determine version from actual changes

  [5] add release date to each version
      format: YYYY-MM-DD
      no time component

  [6] link to issues and commits
      provide traceability
      use (#123) or (abc1234) format

  [7] include migration notes for breaking changes
      tell users how to update their code
      link to detailed migration guides if needed

  [8] update changelog BEFORE release
      not after, not during
      changelog is part of the release

  [9] use active voice in entries
      "Added feature" not "Feature was added"
      start each entry with a verb

  [10] never clear changelog history
      keep all historical entries
      project history is valuable


FINAL REMINDERS


changelog is user-facing documentation

treat it with same care as API docs.
it's often the first thing users check when upgrading.


consistency builds trust

consistent format and quality signals professionalism.
users trust projects with well-maintained changelogs.


changelog enables fearless upgrades

clear migration notes and change descriptions
let users upgrade with confidence.

when in doubt

document more rather than less.
users can skip irrelevant info,
but missing info causes confusion and frustration.

the goal

transparent project history.
clear upgrade paths.
informed user base.

now go document your changes.
