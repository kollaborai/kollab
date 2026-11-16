/**
 * How a user bubble's text maps to its sent images (#1088). A [imageN] token
 * stands for the row's Nth stored image; the splitter keeps the surrounding
 * text so the bubble can swap tokens for thumbnails without losing words.
 */

export type MediaSegment =
  | { kind: "text"; text: string }
  | { kind: "token"; index: number };

const IMAGE_TOKEN_SPLIT = /(\[image\d+\])/g;

const imageTokenIndex = (segment: string): number | null => {
  const match = /^\[image(\d+)\]$/.exec(segment);
  return match ? Number(match[1]) - 1 : null;
};

export function splitMediaSegments(text: string): MediaSegment[] {
  const segments: MediaSegment[] = [];
  for (const segment of text.split(IMAGE_TOKEN_SPLIT)) {
    if (segment === "") continue;
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
