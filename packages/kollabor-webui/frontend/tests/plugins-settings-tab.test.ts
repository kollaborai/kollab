import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// Settings has a Plugins tab that mounts the plugin page; phones open it in the full-screen surface (refs #1028).
const panels = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/components/panels");
const read = (name: string) => readFileSync(path.join(panels, name), "utf8");

describe("settings Plugins tab", () => {
  it("adds a Plugins row to the settings model", () => {
    expect(read("panel-model.ts")).toMatch(/id: "plugins", label: "Plugins"/);
  });

  it("mounts the plugin settings page in the panel host", () => {
    const host = read("PanelHost.tsx");
    expect(host).toMatch(/import \{ PluginsSettings \} from "@\/plugins\/settings"/);
    expect(host).toMatch(/<PluginsSettings\b/);
  });

  it("mounts the plugin page whether or not a session is open", () => {
    const host = read("PanelHost.tsx");
    expect(host).toMatch(/^\s*<PluginsSettings api=\{api\} \/>\s*$/m);
    expect(host).not.toMatch(/sessionId \? <PluginsSettings/);
  });

  it("opens the Plugins tab in the responsive dialog surface", () => {
    expect(read("PanelHost.tsx")).toMatch(/responsive=\{tab === "plugins"\}/);
    expect(read("PanelDialogSurface.tsx")).toMatch(/responsive\?: boolean/);
  });
});
