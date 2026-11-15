import { describe, expect, it, vi } from "vitest";

import {
  loadComposerAttachments,
  saveComposerAttachments,
} from "@/composer-draft";
import {
  collectImageAttachments,
  orphanImageNotice,
  stripImageTokens,
} from "@/composer-restore";

describe("stripImageTokens", () => {
  it("strips managed [imageN] token lines and counts them", () => {
    const result = stripImageTokens("[image1]\n[image2]\nhello world");
    expect(result).toEqual({ text: "hello world", tokenCount: 2 });
  });

  it("keeps text that merely mentions image-like syntax inside a sentence", () => {
    const result = stripImageTokens("see [image1] below");
    expect(result).toEqual({ text: "see [image1] below", tokenCount: 0 });
  });

  it("returns empty text with count when the draft is only tokens", () => {
    const result = stripImageTokens("[image1]\n[image2]");
    expect(result).toEqual({ text: "", tokenCount: 2 });
  });

  it("cleans up leading whitespace and collapsed blank lines after stripping", () => {
    const result = stripImageTokens("[image1]\n\n\ndraft text");
    expect(result.text).toBe("draft text");
  });

  it("leaves token-free drafts untouched", () => {
    expect(stripImageTokens("plain draft")).toEqual({
      text: "plain draft",
      tokenCount: 0,
    });
    expect(stripImageTokens("")).toEqual({ text: "", tokenCount: 0 });
  });
});

describe("collectImageAttachments", () => {
  const image = (id: string) => ({
    id,
    type: "image",
    name: `${id}.png`,
    contentType: "image/png",
    status: { type: "complete" },
    content: [{ type: "image", image: "data:image/png;base64,AAAA" }],
  });

  it("serializes complete image attachments for in-memory retention", () => {
    const stored = collectImageAttachments([image("a1"), image("a2")]);
    expect(stored).toHaveLength(2);
    expect(stored[0]).toMatchObject({ id: "a1", name: "a1.png" });
  });

  it("skips non-image, pending, and content-less attachments", () => {
    const pending = { ...image("p1"), status: { type: "running" } };
    const document = {
      id: "d1",
      type: "document",
      name: "notes.txt",
      status: { type: "complete" },
      content: [{ type: "text", text: "hi" }],
    };
    const noContent = { ...image("n1"), content: [] };
    expect(collectImageAttachments([pending, document, noContent])).toEqual([]);
  });
});

describe("orphanImageNotice", () => {
  it("is null when every token maps to a restored attachment", () => {
    expect(orphanImageNotice(2, 2)).toBeNull();
    expect(orphanImageNotice(0, 0)).toBeNull();
  });

  it("explains that the lost image must be pasted again", () => {
    const notice = orphanImageNotice(1, 0);
    expect(notice).toContain("1 pasted image");
    expect(notice).toContain("paste");
  });

  it("uses the plural for several lost images", () => {
    expect(orphanImageNotice(3, 1)).toContain("2 pasted images");
  });

  it("never fires when more attachments than tokens were restored", () => {
    expect(orphanImageNotice(1, 2)).toBeNull();
  });
});

describe("composer attachment retention per draft key", () => {
  it("restores what was saved for the same key in the same tab", async () => {
    const { loadComposerAttachments: freshLoad, saveComposerAttachments: freshSave } =
      await import("@/composer-draft");
    const stored = [
      { id: "a1", name: "a.png", contentType: "image/png", content: [{ type: "image" }] },
    ];
    freshSave("k1", stored);
    expect(freshLoad("k1")).toEqual(stored);
    expect(loadComposerAttachments("k2")).toEqual([]);
  });

  it("an empty save clears the key's retention", async () => {
    const { loadComposerAttachments: freshLoad, saveComposerAttachments: freshSave } =
      await import("@/composer-draft");
    freshSave("k3", [
      { id: "a1", name: "a.png", contentType: "image/png", content: [] },
    ]);
    freshSave("k3", []);
    expect(freshLoad("k3")).toEqual([]);
  });

  it("retention is memory-only: a reload (module re-import) starts empty", async () => {
    const { saveComposerAttachments: freshSave } = await import("@/composer-draft");
    freshSave("k4", [
      { id: "a1", name: "a.png", contentType: "image/png", content: [] },
    ]);
    vi.resetModules();
    const reloaded = await import("@/composer-draft");
    expect(reloaded.loadComposerAttachments("k4")).toEqual([]);
  });

  it("ignores a null draft key", () => {
    expect(() => saveComposerAttachments(null, [])).not.toThrow();
    expect(loadComposerAttachments(null)).toEqual([]);
  });
});
