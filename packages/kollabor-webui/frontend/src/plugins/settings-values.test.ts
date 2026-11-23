import { describe, expect, it } from "vitest";
import { settingsValues } from "./settings";

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
