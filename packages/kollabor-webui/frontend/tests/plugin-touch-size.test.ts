import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const settingsSource = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/plugins/settings.tsx"), "utf8");
const installSource = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/plugins/install.tsx"), "utf8");

// The rule that makes plugin controls touch-sized (docs/specs/plugins.md, "Right-hand menu").
const css = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/styles.css"), "utf8");
const dialogSource = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/components/ui/dialog.tsx"), "utf8");
const sheetSource = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/components/ui/sheet.tsx"), "utf8");
const sessionPanelSource = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/components/shell/SessionPanel.tsx"), "utf8");
const trajectorySource = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/components/trajectory/TrajectoryView.tsx"), "utf8");
const block = /@media \(pointer: coarse\) \{\s*(\[data-plugin\][^{]+)\{\s*min-height: 44px;/.exec(css);

const escapeRegexText = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

describe("touch size inside [data-plugin]", () => {
  it("escapes backslashes before adding selector text to a regex", () => {
    const selector = "[data\\plugin]";
    const pattern = new RegExp(`^${escapeRegexText(selector)}$`);

    expect(pattern.test(selector)).toBe(true);
    expect(pattern.test("[dataplugin]")).toBe(false);
  });

  it("covers buttons, tabs, links, selects and text fields", () => {
    expect(block, "the coarse-pointer [data-plugin] rule").not.toBeNull();
    for (const control of ["button", '[role="button"]', '[role="tab"]', "a[href]", "select", "input", "textarea"])
      expect(block![1], control).toContain(control);
  });

  it("leaves switches, checkboxes, radios and sliders at their own size", () => {
    for (const control of ['[role="switch"]', '[role="checkbox"]', '[role="radio"]', '[type="checkbox"]', '[type="radio"]', '[type="range"]'])
      expect(block![1], control).toMatch(new RegExp(`:not\\([^)]*${escapeRegexText(control)}`));
  });
});

describe("plugin on/off switch hit area", () => {
  it("sits in a 44x44 wrapper at a coarse pointer, so the clickable box is touch sized while the track stays 32x18", () => {
    const wrapper = /<label[^>]*className="([^"]*)"[^>]*>\s*<Switch\b/.exec(settingsSource);
    expect(wrapper, "a label wrapping the plugin Switch").not.toBeNull();
    for (const cls of ["pointer-coarse:size-11", "pointer-coarse:flex", "pointer-coarse:items-center", "pointer-coarse:justify-center"])
      expect(wrapper![1], cls).toContain(cls);
  });
  it("no longer relies on a pseudo-element the box model does not count", () => {
    expect(settingsSource).not.toContain("pointer-coarse:before:");
  });
});

describe("Add Plugin touch targets", () => {
  it("keeps both source inputs at least 44px high on coarse pointers", () => {
    const inputs = [...installSource.matchAll(/<Input\b[\s\S]*?\/>/g)].map(([input]) => input);
    expect(inputs).toHaveLength(2);
    for (const input of inputs)
      expect(input).toContain("pointer-coarse:min-h-11");
  });
});

describe("touch close-button spacing", () => {
  it("reserves title space beside enlarged Dialog and Sheet close buttons", () => {
    expect(dialogSource).toMatch(/data-slot="dialog-header"[\s\S]*?className=\{cn\("[^"]*pointer-coarse:pr-12/);
    expect(sheetSource).toMatch(/data-slot="sheet-header"[\s\S]*?className=\{cn\("[^"]*pointer-coarse:pr-12/);
  });

  it("keeps the mobile session sheet content below the enlarged close target", () => {
    expect(sessionPanelSource).toMatch(/<SheetContent[^>]*className="[^"]*pointer-coarse:pt-14/);
  });

  it("keeps the mobile trajectory content below the enlarged close target", () => {
    expect(trajectorySource).toMatch(/<SheetContent[^>]*className="[^"]*pointer-coarse:pt-14/);
  });
});
