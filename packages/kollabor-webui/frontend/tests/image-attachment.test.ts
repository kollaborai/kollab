import { describe, expect, it } from "vitest";
import type { CompleteAttachment } from "@assistant-ui/react";
import { imageAttachmentAdapter, toInputAttachments } from "../src/image-attachment";

const dataUrl = "data:image/png;base64,iVBORw0KGgo=";

const completedImage: CompleteAttachment = {
  id: "att-1",
  type: "image",
  name: "photo.png",
  contentType: "image/png",
  status: { type: "complete" },
  content: [{ type: "image", image: dataUrl }],
};

describe("image attachment adapter and input mapping", () => {
  it("toInputAttachments maps one completed image attachment to {name, content_type, data_url}", () => {
    expect(toInputAttachments([completedImage])).toEqual([
      { name: "photo.png", content_type: "image/png", data_url: dataUrl },
    ]);
  });

  it("toInputAttachments returns undefined for an empty list and for undefined", () => {
    expect(toInputAttachments([])).toBeUndefined();
    expect(toInputAttachments(undefined)).toBeUndefined();
  });

  it("imageAttachmentAdapter.add({file}) keeps the current behavior: the attachment has status requires-action", async () => {
    const file = new File(["png-bytes"], "photo.png", { type: "image/png" });
    const pending = await imageAttachmentAdapter.add({ file });
    expect(pending.status).toEqual({ type: "requires-action", reason: "composer-send" });
  });
});
