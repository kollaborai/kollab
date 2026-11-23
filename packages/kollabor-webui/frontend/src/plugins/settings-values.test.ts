import { describe, expect, it } from "vitest";
import { pluginRowControls, settingsValues } from "./settings";

describe("plugin settings payload", () => {
  it("converts installed and development panel paths to plugin-local keys", () => {
    expect(settingsValues({ id: "hello", kind: "installed" }, {
      "plugin-values.hello.greeting": "Howdy",
      "plugin-values.hello.nested.key": 3,
    })).toEqual({ greeting: "Howdy", "nested.key": 3 });
    expect(settingsValues({ id: "hello@dev", kind: "dev" }, {
      "plugin-values.hello@dev.greeting": "Hi",
    })).toEqual({ greeting: "Hi" });
  });

  it("preserves built-in paths and normalizes cleared secrets", () => {
    expect(settingsValues({ id: "terminal", kind: "built-in" }, {
      "plugins.terminal.setting": 1,
      "plugins.terminal.secret": null,
    })).toEqual({ "plugins.terminal.setting": 1, "plugins.terminal.secret": "" });
  });

  it("rejects an empty or foreign plugin path", () => {
    expect(() => settingsValues({ id: "hello", kind: "installed" }, {
      "plugin-values.other.key": "bad",
    })).toThrow("Invalid plugin setting path");
    expect(() => settingsValues({ id: "hello", kind: "installed" }, {
      "plugin-values.hello.": "bad",
    })).toThrow("Invalid plugin setting path");
  });
});

describe("plugin row controls", () => {
  const row = (kind: "built-in" | "installed" | "dev", state = "ok", shadowed = false) =>
    ({ kind, state, shadowed }) as Parameters<typeof pluginRowControls>[0];

  it("gives a built-in plugin the on/off switch, but no Update, Remove or service controls", () => {
    expect(pluginRowControls(row("built-in")).switchEnabled).toBe(true);
    expect(pluginRowControls(row("built-in")).updateRemove).toBe(false);
    expect(pluginRowControls(row("built-in")).services).toBe(false);
  });

  it("gives Update and Remove to installed plugins only", () => {
    expect(pluginRowControls(row("installed")).updateRemove).toBe(true);
    expect(pluginRowControls(row("dev")).updateRemove).toBe(false);
  });

  it("holds the switch while a plugin is stopping or shadowed", () => {
    expect(pluginRowControls(row("installed", "stopping")).switchEnabled).toBe(false);
    expect(pluginRowControls(row("dev", "ok", true)).switchEnabled).toBe(false);
  });
});
