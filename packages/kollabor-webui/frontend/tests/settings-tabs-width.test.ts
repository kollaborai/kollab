import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// Settings sections wrap onto a second row at 820px instead of scrolling one crowded row (refs #1028).
const source = readFileSync(
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/components/panels/PanelHost.tsx"),
  "utf8",
);
const classesOf = (pattern: RegExp): string[] => (pattern.exec(source)?.[1] ?? "").split(/\s+/);
const tabsList = /aria-label="Settings sections"\s+className="([^"]+)"/;
const sectionTrigger = /key=\{item\.id\}\s+value=\{item\.id\}\s+className="([^"]+)"/;

describe("settings section tabs at 820px", () => {
  it("wraps the section list instead of pinning it to one scrolling row on md", () => {
    expect(source).toMatch(tabsList);
    for (const cls of ["md:h-9", "md:flex-nowrap", "md:overflow-x-auto"])
      expect(classesOf(tabsList), cls).not.toContain(cls);
  });

  it("does not stretch each section tab to the bar height on md", () => {
    expect(source).toMatch(sectionTrigger);
    expect(classesOf(sectionTrigger), "md:h-full").not.toContain("md:h-full");
  });
});
