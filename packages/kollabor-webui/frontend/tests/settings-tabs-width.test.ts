import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// Settings section class contracts keep the tab row wrapped and each tab content-sized (refs #1028).
const source = readFileSync(
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/components/panels/PanelHost.tsx"),
  "utf8",
);
const openingProps = (tag: string): string => new RegExp(`<${tag}\\b([^>]*)>`, "s").exec(source)?.[1] ?? "";
const classesOf = (tag: string): string[] => /className="([^"]+)"/.exec(openingProps(tag))?.[1].split(/\s+/) ?? [];

describe("settings section tab layout classes", () => {
  it("wraps the section list instead of pinning it to one scrolling row on md", () => {
    expect(classesOf("TabsList")).toContain("flex-wrap");
    for (const cls of ["md:h-9", "md:flex-nowrap", "md:overflow-x-auto"])
      expect(classesOf("TabsList"), cls).not.toContain(cls);
  });

  it("does not stretch each section tab to the bar height on md", () => {
    expect(classesOf("TabsTrigger"), "md:h-full").not.toContain("md:h-full");
  });
});
