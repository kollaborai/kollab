import assert from "node:assert/strict";
import test from "node:test";
import { parseChangelog, whatsNewFromMarkdown } from "../src/learn/changelog.ts";

test("What's New parser reads Unreleased and the newest release from Markdown", () => {
  const sections = parseChangelog(`# Changelog

## [Unreleased]
### Added
- Add the Learn page.

## [0.17.0] - 2026-10-19
### Changed
- Improve workspace labels.

## [0.16.0] - 2026-10-01
### Fixed
- An older entry that should not be shown.
`);
  assert.equal(sections.length, 2);
  assert.equal(sections[0].version, "Unreleased");
  assert.deepEqual(sections[0].groups[0], {
    category: "Added",
    items: ["Add the Learn page."],
  });
  assert.equal(sections[1].version, "0.17.0");
  assert.equal(sections[1].date, "2026-10-19");
});

test("What's New ignores empty sections and handles an empty or malformed changelog", () => {
  assert.deepEqual(parseChangelog("## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n"), []);
  assert.equal(whatsNewFromMarkdown("## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n").status, "empty");
  assert.equal(whatsNewFromMarkdown("").status, "empty");
  assert.equal(whatsNewFromMarkdown("not a changelog").status, "error");
  const sections = parseChangelog("## Unreleased\n- New item with `inline code`.\n");
  assert.deepEqual(sections[0].groups[0].items, ["New item with inline code."]);
});
