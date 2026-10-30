import { afterEach, describe, expect, it, vi } from "vitest";
import { appRelink } from "../src/phonelink/loader/app-shell";

afterEach(() => { delete (globalThis as Record<string, unknown>).__KOLLAB_APP__; });

describe("appRelink", () => {
  it("returns null outside the phone app", () => {
    expect(appRelink()).toBeNull();
  });

  it("returns null when the app's relink is not a function", () => {
    (globalThis as Record<string, unknown>).__KOLLAB_APP__ = { relink: "x" };
    expect(appRelink()).toBeNull();
  });

  it("calls the app's relink once, with the app as this", () => {
    const app = { relink: vi.fn(function (this: unknown) { return this; }) };
    (globalThis as Record<string, unknown>).__KOLLAB_APP__ = app;
    const relink = appRelink();
    expect(relink).toBeTypeOf("function");
    relink?.();
    expect(app.relink).toHaveBeenCalledTimes(1);
    expect(app.relink.mock.contexts[0]).toBe(app);
  });
});
