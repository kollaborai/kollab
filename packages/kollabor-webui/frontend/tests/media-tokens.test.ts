import { describe, expect, it } from "vitest";

import { splitMediaSegments } from "@/components/media/media-tokens";

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

  it("survives multiline text", () => {
    const segments = splitMediaSegments("line one\n[image2]\nline two");
    expect(segments).toEqual([
      { kind: "text", text: "line one\n" },
      { kind: "token", index: 1 },
      { kind: "text", text: "\nline two" },
    ]);
  });
});
