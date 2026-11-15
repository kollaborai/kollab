import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

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

  it("preserves image-like text inline with the draft", () => {
    const result = stripImageTokens("[image1]hello from alpha");
    expect(result).toEqual({ text: "[image1]hello from alpha", tokenCount: 0 });
  });

  it("preserves ordinary draft text that happens to contain an image-like token", () => {
    expect(stripImageTokens("Please print [image1] literally")).toEqual({
      text: "Please print [image1] literally",
      tokenCount: 0,
    });
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
  const pendingImage = (id: string) => ({
    id,
    type: "image",
    name: `${id}.png`,
    contentType: "image/png",
    status: { type: "requires-action", reason: "composer-send" },
    file: new File([], `${id}.png`, { type: "image/png" }),
  });
  const completeImage = (id: string) => ({
    id,
    type: "image",
    name: `${id}.png`,
    contentType: "image/png",
    status: { type: "complete" },
    content: [{ type: "image", image: "data:image/png;base64,AAAA" }],
  });

  it("retains pending image attachments (the composer's pre-send state)", () => {
    const stored = collectImageAttachments([pendingImage("a1"), pendingImage("a2")]);
    expect(stored).toHaveLength(2);
    expect(stored[0]?.file).toBeInstanceOf(File);
    expect(stored[0]?.name).toBe("a1.png");
  });

  it("retains complete image attachments by their content parts", () => {
    const stored = collectImageAttachments([completeImage("a1")]);
    expect(stored).toHaveLength(1);
    expect(stored[0]?.file).toBeUndefined();
    expect(stored[0]?.content).toHaveLength(1);
  });

  it("skips non-image attachments and images with neither file nor content", () => {
    const document = {
      id: "d1",
      type: "document",
      name: "notes.txt",
      status: { type: "complete" },
      content: [{ type: "text", text: "hi" }],
    };
    const bare = {
      id: "b1",
      type: "image",
      name: "bare.png",
      contentType: "image/png",
      status: { type: "requires-action", reason: "composer-send" },
    };
    expect(collectImageAttachments([document, bare])).toEqual([]);
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
      {
        id: "a1",
        type: "image",
        name: "a.png",
        contentType: "image/png",
        file: new File([], "a.png", { type: "image/png" }),
      },
    ];
    freshSave("k1", stored);
    expect(freshLoad("k1")).toEqual(stored);
    expect(loadComposerAttachments("k2")).toEqual([]);
  });

  it("an empty save clears the key's retention", async () => {
    const { loadComposerAttachments: freshLoad, saveComposerAttachments: freshSave } =
      await import("@/composer-draft");
    freshSave("k3", [
      { id: "a1", type: "image", name: "a.png", contentType: "image/png" },
    ]);
    freshSave("k3", []);
    expect(freshLoad("k3")).toEqual([]);
  });

  it("retention is memory-only: a reload (module re-import) starts empty", async () => {
    const { saveComposerAttachments: freshSave } = await import("@/composer-draft");
    freshSave("k4", [
      { id: "a1", type: "image", name: "a.png", contentType: "image/png" },
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

describe("composer restore wiring", () => {
  const threadSource = readFileSync(
    fileURLToPath(
      new URL("../src/components/assistant-ui/thread.tsx", import.meta.url),
    ),
    "utf8",
  );

  it("restores retained attachments through the live composer and sends them", () => {
    expect(threadSource).toContain("const keptImages = loadComposerAttachments(key)");
    expect(threadSource).toContain("aui.composer.addAttachment(image.file)");
    expect(threadSource).toContain("role=\"status\"");
    expect(threadSource).toContain("for (const attachment of state.attachments)");
  });

  it("reports only images that failed to reattach", () => {
    expect(threadSource).toContain(
      "orphanImageNotice(restored.tokenCount, restoredImageCount)",
    );
  });
});
