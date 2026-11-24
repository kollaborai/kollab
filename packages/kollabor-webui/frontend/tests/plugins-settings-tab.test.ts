import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "@babel/parser";
import { describe, expect, it } from "vitest";
import { SETTINGS_TABS } from "../src/components/panels/panel-model";

// Settings has a Plugins tab that mounts the plugin page; phones open it in the full-screen surface (refs #1028).
const panels = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/components/panels");
const read = (name: string) => readFileSync(path.join(panels, name), "utf8");

type AstNode = Record<string, unknown> & { type?: string };
function pluginMounts(source: string): Array<{ ancestors: AstNode[] }> {
  const ast = parse(source, { sourceType: "module", plugins: ["typescript", "jsx"] });
  const mounts: Array<{ ancestors: AstNode[] }> = [];
  const visit = (node: unknown, ancestors: AstNode[] = []) => {
    if (!node || typeof node !== "object") return;
    const current = node as AstNode;
    if (current.type === "JSXElement") {
      const opening = current.openingElement as AstNode;
      const name = opening.name as AstNode;
      if (name.name === "PluginsSettings") mounts.push({ ancestors });
    }
    for (const value of Object.values(current)) {
      if (Array.isArray(value)) value.forEach((child) => visit(child, [...ancestors, current]));
      else if (value && typeof value === "object") visit(value, [...ancestors, current]);
    }
  };
  visit(ast);
  return mounts;
}

function jsxAttribute(element: AstNode, name: string): unknown {
  const opening = element.openingElement as AstNode;
  return (opening.attributes as AstNode[]).find((attribute) => {
    const key = attribute.name as AstNode | undefined;
    return key?.name === name;
  })?.value && (((opening.attributes as AstNode[]).find((attribute) => {
    const key = attribute.name as AstNode | undefined;
    return key?.name === name;
  })?.value as AstNode).value);
}

describe("settings Plugins tab", () => {
  it("adds a Plugins row to the settings model", () => {
    expect(SETTINGS_TABS.find((item) => item.id === "plugins")).toMatchObject({ label: "Plugins" });
  });

  it("renders the plugin page inside the Plugins panel without a session gate", () => {
    const host = read("PanelHost.tsx");
    const mounts = pluginMounts(host);
    expect(mounts).toHaveLength(1);
    const panelsWithPlugins = mounts[0].ancestors.filter((ancestor) => {
      if (ancestor.type !== "JSXElement") return false;
      return jsxAttribute(ancestor, "aria-label") === "Plugins";
    });
    expect(panelsWithPlugins).toHaveLength(1);
    expect(mounts[0].ancestors.filter((ancestor) => ancestor.type === "ConditionalExpression").some((ancestor) =>
      JSON.stringify(ancestor.test).includes('"name":"sessionId"'))).toBe(false);
  });

  it("keeps the same mobile sheet mounted across Settings sections", () => {
    const host = read("PanelHost.tsx");
    expect(host).not.toMatch(/isMobile\s*&&\s*tab\s*!==\s*"plugins"/);
    expect(host).toMatch(/isMobile\s*\?\s*\(\s*<SheetContent/);
    expect(host).not.toMatch(/responsive=\{tab === "plugins"\}/);
    expect(read("PanelDialogSurface.tsx")).not.toMatch(/responsive\?: boolean/);
  });
});
