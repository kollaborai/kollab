import type { ImageMessagePart, TextMessagePart, ThreadMessageLike } from "@assistant-ui/react";

/**
 * A stored turn's content as the thread shows it. A picture the engine stored as
 * a managed_upload has no public URL, so it shows as the placeholder text
 * [imageN], the same token the engine's content_to_text writes.
 */
export function historyContentToThreadContent(content: unknown): ThreadMessageLike["content"] {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";

  let imageIndex = 0;
  const parts: Array<TextMessagePart | ImageMessagePart> = [];
  for (const rawPart of content) {
    if (!rawPart || typeof rawPart !== "object") continue;
    const part = rawPart as Record<string, unknown>;
    if (part.type === "text" && typeof part.text === "string") {
      parts.push({ type: "text", text: part.text });
      continue;
    }
    if (part.type !== "image") continue;
    imageIndex += 1;

    const source =
      part.source && typeof part.source === "object"
        ? (part.source as Record<string, unknown>)
        : undefined;
    const image =
      typeof part.image === "string"
        ? part.image
        : source?.kind === "url" && typeof source.url === "string"
          ? source.url
          : undefined;
    if (image) {
      parts.push({ type: "image", image });
    } else {
      parts.push({ type: "text", text: `[image${imageIndex}]` });
    }
  }
  return parts.length ? parts : "";
}
