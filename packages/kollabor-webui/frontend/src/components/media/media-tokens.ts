/**
 * How a user bubble's text maps to its sent images (#1088). A [imageN] token
 * stands for the row's Nth stored image; the splitter keeps the surrounding
 * text so the bubble can swap tokens for thumbnails without losing words.
 */

export type MediaSegment =
  | { kind: "text"; text: string }
  | { kind: "literal"; text: string }
  | { kind: "token"; index: number };

// [[imageN]] is the escaped literal form (#1124 P2a): it renders as plain
// [imageN] text, consumes no media slot, and the engine's counter ignores it.
const IMAGE_TOKEN_SPLIT = /(\[\[image\d+\]\]|\[image\d+\])/g;

const escapedTokenIndex = (segment: string): string | null => {
  const match = /^\[\[image(\d+)\]\]$/.exec(segment);
  return match ? match[1] : null;
};

const imageTokenIndex = (segment: string): number | null => {
  const match = /^\[image(\d+)\]$/.exec(segment);
  return match ? Number(match[1]) - 1 : null;
};

export function splitMediaSegments(text: string): MediaSegment[] {
  const segments: MediaSegment[] = [];
  for (const segment of text.split(IMAGE_TOKEN_SPLIT)) {
    if (segment === "") continue;
    const escaped = escapedTokenIndex(segment);
    if (escaped !== null) {
      segments.push({ kind: "literal", text: `[image${escaped}]` });
      continue;
    }
    const index = imageTokenIndex(segment);
    // The engine numbers sent images from 1; anything else is just text,
    // joined back onto the text around it so near-misses stay one run.
    if (index === null || index < 0) {
      const last = segments[segments.length - 1];
      if (last && last.kind === "text") last.text += segment;
      else segments.push({ kind: "text", text: segment });
    } else {
      segments.push({ kind: "token", index });
    }
  }
  return segments;
}

import type { MediaEntry } from "@/api";

/** What a user bubble renders for one segment. A token resolves to its
 * slot's entry only when that slot holds a stored image; a missing or
 * failed slot stays literal [imageN] text - it never requests a ref and
 * never opens the viewer (#1124 P2a). */
export type ResolvedSegment =
  | { kind: "text"; text: string }
  | { kind: "literal"; text: string }
  | { kind: "thumb"; entry: MediaEntry; index: number };

export function resolveMediaSegment(
  segment: MediaSegment,
  media: MediaEntry[] | undefined,
): ResolvedSegment {
  if (segment.kind === "token") {
    const entry = media?.[segment.index];
    if (entry && !entry.failed && entry.ref) {
      return { kind: "thumb", entry, index: segment.index };
    }
    return { kind: "literal", text: `[image${segment.index + 1}]` };
  }
  if (segment.kind === "literal") return { kind: "literal", text: segment.text };
  return { kind: "text", text: segment.text };
}
