import { expect, test } from "vitest";
import { shortPath } from "../src/components/ui/path-text";

test("a long path keeps its root and last parts", () => {
  expect(shortPath("/private/tmp/a/b/ws/.kollab/config.json", 3)).toBe("/…/ws/.kollab/config.json");
  expect(shortPath("~/dev/platform/mentiko/web/.kollab/config.json", 3)).toBe("~/…/web/.kollab/config.json");
});

test("a path that would hide one folder or none stays whole", () => {
  expect(shortPath("~/dev/kollab/.kollab/config.json", 3)).toBe("~/dev/kollab/.kollab/config.json");
  expect(shortPath("~/.kollab/voice/preferences.json", 3)).toBe("~/.kollab/voice/preferences.json");
  expect(shortPath("/a/b/c/d/e/f")).toBe("/a/b/c/d/e/f");
});
