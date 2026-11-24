import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// The rule that makes plugin controls touch-sized (docs/specs/plugins.md, "Right-hand menu").
const css = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/styles.css"), "utf8");
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

describe("NoticeAction touch size", () => {
  it("keeps the coarse-pointer 44px minimum when a caller passes className", async () => {
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    const { NoticeAction } = await import("@/components/shell/NoticeBar");
    const html = renderToStaticMarkup(createElement(NoticeAction, { className: "max-lg:h-11" }, "Retry"));
    expect(html).toContain("pointer-coarse:min-h-11");
    expect(html).toContain("max-lg:h-11");
  });
});
