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
      { kind: "token", index: 0, raw: "[image1]" },
      { kind: "text", text: " and " },
      { kind: "token", index: 1, raw: "[image2]" },
    ]);
  });

  it("drops empty segments around leading or trailing tokens", () => {
    expect(splitMediaSegments("[image1]")).toEqual([{ kind: "token", index: 0, raw: "[image1]" }]);
  });

  it("keeps near-miss text as text", () => {
    expect(splitMediaSegments("[image] [imageX] [image0] see")).toEqual([
      { kind: "text", text: "[image] [imageX] [image0] see" },
    ]);
  });

  it("keeps higher-numbered tokens that have no stored copy", () => {
    expect(splitMediaSegments("a [image3] b")).toEqual([
      { kind: "text", text: "a " },
      { kind: "token", index: 2, raw: "[image3]" },
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
      { kind: "token", index: 0, raw: "[image1]" },
    ]);
  });

  it("survives multiline text", () => {
    const segments = splitMediaSegments("line one\n[image2]\nline two");
    expect(segments).toEqual([
      { kind: "text", text: "line one\n" },
      { kind: "token", index: 1, raw: "[image2]" },
      { kind: "text", text: "\nline two" },
    ]);
  });
});

describe("#1140 R3/R4 grammar + exact spelling", () => {
  it("treats non-ASCII digit tokens as plain text (ASCII grammar, engine-aligned)", () => {
    // The engine counter must not count [image1١] either; the mismatch
    // made the engine bind a stored image to a row whose text was literal.
    expect(splitMediaSegments("see [image1١] now")).toEqual([
      { kind: "text", text: "see [image1١] now" },
    ]);
  });

  it("keeps a padded token's exact spelling as raw (R4)", () => {
    expect(splitMediaSegments("padded [image01] end")).toEqual([
      { kind: "text", text: "padded " },
      { kind: "token", index: 0, raw: "[image01]" },
      { kind: "text", text: " end" },
    ]);
  });

  it("keeps an escaped padded literal's spelling (R4)", () => {
    expect(splitMediaSegments("typed [[image01]] here")).toEqual([
      { kind: "text", text: "typed " },
      { kind: "literal", text: "[image01]" },
      { kind: "text", text: " here" },
    ]);
  });

  it("renders an unresolved padded token with its EXACT spelling (R4, RED)", () => {
    // The old resolver reconstructed [image01] as [image1]; unresolved and
    // failed tokens must keep their original spelling.
    const failed = { ref: "", mime: "", failed: true };
    expect(resolveMediaSegment({ kind: "token", index: 0, raw: "[image01]" }, [failed]))
      .toEqual({ kind: "literal", text: "[image01]" });
    expect(resolveMediaSegment({ kind: "token", index: 0, raw: "[image01]" }, undefined))
      .toEqual({ kind: "literal", text: "[image01]" });
  });

  it("still resolves a resolvable padded token to its slot (control)", () => {
    const stored = { ref: "r-1", mime: "image/png" };
    expect(resolveMediaSegment({ kind: "token", index: 0, raw: "[image01]" }, [stored]))
      .toEqual({ kind: "thumb", entry: stored, index: 0 });
  });
});

describe("#1148 image-slot grammar parity", () => {
  it("counts escaped, valid, zero, and malformed bracket runs like the engine", () => {
    const cases: Array<[string, number]> = [
      ["[image1]", 1],
      ["[[image1]]", 0],
      ["[image01]", 1],
      ["[image0]", 0],
      ["[image00]", 0],
      ["[[image1]", 1],
      ["[image1]]", 1],
      ["[[image1] then [image2]", 2],
      ["[image1١]", 0],
    ];

    for (const [text, count] of cases) {
      expect(
        splitMediaSegments(text).filter((segment) => segment.kind === "token"),
        text,
      ).toHaveLength(count);
    }
  });
});

describe("resolveMediaSegment", () => {
  const stored = { ref: "r-2", mime: "image/png" };

  it("resolves a token with a stored slot to its entry", () => {
    expect(resolveMediaSegment({ kind: "token", index: 1, raw: "[image2]" }, [{ ref: "r-1", mime: "image/png" }, stored]))
      .toEqual({ kind: "thumb", entry: stored, index: 1 });
  });

  it("keeps a failed slot literal - it never binds (#1124 P2a)", () => {
    const media = [{ ref: "", mime: "", failed: true }, stored];
    expect(resolveMediaSegment({ kind: "token", index: 0, raw: "[image1]" }, media))
      .toEqual({ kind: "literal", text: "[image1]" });
    expect(resolveMediaSegment({ kind: "token", index: 1, raw: "[image2]" }, media))
      .toEqual({ kind: "thumb", entry: stored, index: 1 });
  });

  it("keeps a missing slot and an empty-ref slot literal", () => {
    expect(resolveMediaSegment({ kind: "token", index: 3, raw: "[image4]" }, undefined))
      .toEqual({ kind: "literal", text: "[image4]" });
    expect(resolveMediaSegment({ kind: "token", index: 0, raw: "[image1]" }, [{ ref: "", mime: "" }]))
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
