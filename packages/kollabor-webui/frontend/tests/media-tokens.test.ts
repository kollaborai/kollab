import { describe, expect, it } from "vitest";

import { resolveMediaSegment, splitMediaSegments } from "@/components/media/media-tokens";

describe("splitMediaSegments", () => {
  it("keeps plain text as one segment", () => {
    expect(splitMediaSegments("hello world")).toEqual([
      { kind: "text", text: "hello world" },
    ]);
  });

  it("maps [imageN] to the row's Nth image (0-based)", () => {
    expect(splitMediaSegments("look [image1] and [image2]")).toEqual([
      { kind: "text", text: "look " },
      { kind: "token", index: 0 },
      { kind: "text", text: " and " },
      { kind: "token", index: 1 },
    ]);
  });

  it("drops empty segments around leading or trailing tokens", () => {
    expect(splitMediaSegments("[image1]")).toEqual([{ kind: "token", index: 0 }]);
  });

  it("keeps near-miss text as text", () => {
    expect(splitMediaSegments("[image] [imageX] [image0] see")).toEqual([
      { kind: "text", text: "[image] [imageX] [image0] see" },
    ]);
  });

  it("keeps higher-numbered tokens that have no stored copy", () => {
    expect(splitMediaSegments("a [image3] b")).toEqual([
      { kind: "text", text: "a " },
      { kind: "token", index: 2 },
      { kind: "text", text: " b" },
    ]);
  });

  it("keeps escaped [[imageN]] as literal text, not a token", () => {
    expect(splitMediaSegments("see [[image1]] below")).toEqual([
      { kind: "text", text: "see " },
      { kind: "literal", text: "[image1]" },
      { kind: "text", text: " below" },
    ]);
  });

  it("mixes escaped and real tokens", () => {
    expect(splitMediaSegments("[[image1]] then [image1]")).toEqual([
      { kind: "literal", text: "[image1]" },
      { kind: "text", text: " then " },
      { kind: "token", index: 0 },
    ]);
  });

  it("survives multiline text", () => {
    const segments = splitMediaSegments("line one\n[image2]\nline two");
    expect(segments).toEqual([
      { kind: "text", text: "line one\n" },
      { kind: "token", index: 1 },
      { kind: "text", text: "\nline two" },
    ]);
  });
});

describe("resolveMediaSegment", () => {
  const stored = { ref: "r-2", mime: "image/png" };

  it("resolves a token with a stored slot to its entry", () => {
    expect(resolveMediaSegment({ kind: "token", index: 1 }, [{ ref: "r-1", mime: "image/png" }, stored]))
      .toEqual({ kind: "thumb", entry: stored, index: 1 });
  });

  it("keeps a failed slot literal - it never binds (#1124 P2a)", () => {
    const media = [{ ref: "", mime: "", failed: true }, stored];
    expect(resolveMediaSegment({ kind: "token", index: 0 }, media))
      .toEqual({ kind: "literal", text: "[image1]" });
    expect(resolveMediaSegment({ kind: "token", index: 1 }, media))
      .toEqual({ kind: "thumb", entry: stored, index: 1 });
  });

  it("keeps a missing slot and an empty-ref slot literal", () => {
    expect(resolveMediaSegment({ kind: "token", index: 3 }, undefined))
      .toEqual({ kind: "literal", text: "[image4]" });
    expect(resolveMediaSegment({ kind: "token", index: 0 }, [{ ref: "", mime: "" }]))
      .toEqual({ kind: "literal", text: "[image1]" });
  });

  it("passes escaped literals through as literal", () => {
    expect(resolveMediaSegment({ kind: "literal", text: "[image1]" }, undefined))
      .toEqual({ kind: "literal", text: "[image1]" });
  });

  it("passes plain text through", () => {
    expect(resolveMediaSegment({ kind: "text", text: "hi" }, undefined))
      .toEqual({ kind: "text", text: "hi" });
  });
});
