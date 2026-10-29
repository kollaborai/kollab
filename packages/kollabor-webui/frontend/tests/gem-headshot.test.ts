import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { captureHeadshot, headshotSignature } from "../src/components/gems/gem-headshot";
import type { GemProps } from "../src/components/gems/gem-engine";

const fakeCanvas = () => ({
  width: 0, height: 0,
  getContext: () => ({ drawImage: () => undefined }),
  toBlob: (done: (b: Blob | null) => void) => done(new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" })),
});
const settle = () => new Promise((r) => setTimeout(r, 0));
const source = { width: 256, height: 256 } as unknown as HTMLCanvasElement;

describe("headshotSignature", () => {
  it("ignores activity, size and live", () => {
    const resting = { gem: "lapis", size: 32, live: true, activity: "idle" } as GemProps;
    const typing = { gem: "lapis", size: 48, live: false, activity: "typing" } as GemProps;
    expect(headshotSignature(typing)).toBe(headshotSignature(resting));
  });

  it("changes when the hat changes", () => {
    const crown = { gem: "lapis", size: 32, hat: "crown" } as GemProps;
    const beret = { gem: "lapis", size: 32, hat: "beret" } as GemProps;
    expect(headshotSignature(crown)).not.toBe(headshotSignature(beret));
  });
});

describe("captureHeadshot", () => {
  beforeEach(() => {
    vi.stubGlobal("document", { createElement: () => fakeCanvas() });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uploads once per look", async () => {
    const api = { saveGemHeadshot: vi.fn(async () => undefined) };
    const props = { gem: "lapis", size: 32, hat: "crown" } as GemProps;
    captureHeadshot(api as never, "lapis", props, source);
    captureHeadshot(api as never, "lapis", props, source);
    captureHeadshot(api as never, "lapis", props, source);
    await settle();
    await settle();
    expect(api.saveGemHeadshot).toHaveBeenCalledTimes(1);
    captureHeadshot(api as never, "lapis", { ...props, hat: "beret" } as GemProps, source);
    await settle();
    await settle();
    expect(api.saveGemHeadshot).toHaveBeenCalledTimes(2);
  });

  it("uploads nothing without a key", async () => {
    const api = { saveGemHeadshot: vi.fn(async () => undefined) };
    captureHeadshot(api as never, "", { gem: "lapis", size: 32 } as GemProps, source);
    await settle();
    await settle();
    expect(api.saveGemHeadshot).not.toHaveBeenCalled();
  });
});
