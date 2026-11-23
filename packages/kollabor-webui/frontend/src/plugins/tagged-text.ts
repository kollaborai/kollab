export type TaggedTextPart = { type: "text"; text: string } | { type: "tag"; name: string; content: string };

type Open = { name: string; close: number };
type Mask = { start: number; end: number };

const TAG = /<(\/?)([a-z][\w-]*)>/gi;
const FENCE_OPEN = / {0,3}(`{3,}|~{3,})/y;
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})\s*$/;
const INDENTED = /(?: {4}| {0,3}\t)/y;

/** Split only registered, complete tags. Tag content is returned raw; code spans, fences, indented blocks and escaped `<` stay text. */
export function splitPluginTags(text: string, names: readonly string[]): TaggedTextPart[] {
  const allowed = new Set(names.filter((name) => /^[a-z][\w-]*$/i.test(name)));
  const opens = pairTags(text, allowed, maskedRanges(text));
  const parts: TaggedTextPart[] = [];
  let cursor = 0;
  for (const [index, open] of opens) {
    if (open.close < 0 || index < cursor) continue;
    if (cursor < index) parts.push({ type: "text", text: text.slice(cursor, index) });
    parts.push({ type: "tag", name: open.name, content: text.slice(index + open.name.length + 2, open.close) });
    cursor = open.close + open.name.length + 3;
  }
  if (cursor < text.length || !parts.length) parts.push({ type: "text", text: text.slice(cursor) });
  return parts;
}

/** Pair open tags with closes in one pass: a close takes the nearest pending open of its name. Tags inside a masked range are ignored. */
function pairTags(text: string, allowed: ReadonlySet<string>, masked: readonly Mask[]): Map<number, Open> {
  const opens = new Map<number, Open>();
  const pending = new Map<string, Open[]>();
  let mask = 0;
  for (const match of text.matchAll(TAG)) {
    const [, slash, name] = match;
    const index = match.index ?? 0;
    if (!name || !allowed.has(name)) continue;
    while (mask < masked.length && masked[mask].end <= index) mask++;
    if (mask < masked.length && masked[mask].start <= index) continue;
    if (slash) {
      const open = pending.get(name)?.pop();
      if (open) open.close = index;
      continue;
    }
    const open: Open = { name, close: -1 };
    opens.set(index, open);
    const stack = pending.get(name);
    if (stack) stack.push(open);
    else pending.set(name, [open]);
  }
  return opens;
}

/** Ranges no tag may open or close in: code blocks, code spans and backslash escapes, in text order. */
function maskedRanges(text: string): Mask[] {
  const masked: Mask[] = [];
  let index = 0;
  while (index < text.length) {
    if (index === 0 || text[index - 1] === "\n") {
      const blockEnd = codeBlockEnd(text, index);
      if (blockEnd >= 0) {
        masked.push({ start: index, end: blockEnd });
        index = blockEnd;
        continue;
      }
    }
    if (text[index] === "\\") {
      masked.push({ start: index, end: index + 2 });
      index += 2;
      continue;
    }
    if (text[index] === "`") {
      const end = codeSpanEnd(text, index);
      masked.push({ start: index, end });
      index = end;
      continue;
    }
    index++;
  }
  return masked;
}

/** Index just past the code span that opens at this backtick run. The closer is a run of exactly the same length; with none, the opening run is literal text. */
function codeSpanEnd(text: string, index: number): number {
  let runEnd = index;
  while (text[runEnd] === "`") runEnd++;
  const length = runEnd - index;
  // ponytail: an unmatched run scans to the end, so many unmatched runs cost O(n) each. Index runs by length if this shows up.
  let start = text.indexOf("`", runEnd);
  while (start >= 0) {
    let end = start;
    while (text[end] === "`") end++;
    if (end - start === length) return end;
    start = text.indexOf("`", end);
  }
  return runEnd;
}

/** Where the code block that starts at this line start ends, or -1 when none starts here. */
function codeBlockEnd(text: string, index: number): number {
  INDENTED.lastIndex = index;
  if (INDENTED.test(text)) return lineEnd(text, index);
  FENCE_OPEN.lastIndex = index;
  const marker = FENCE_OPEN.exec(text)?.[1];
  return marker ? fenceEnd(text, index, marker) : -1;
}

/** Index just past the newline that ends the line holding `index`, or the text end. */
function lineEnd(text: string, index: number): number {
  const newline = text.indexOf("\n", index);
  return newline < 0 ? text.length : newline + 1;
}

/** Index just past the closing fence, or the text end when the fence never closes. */
function fenceEnd(text: string, index: number, marker: string): number {
  let newline = text.indexOf("\n", index);
  while (newline >= 0) {
    const start = newline + 1;
    const next = text.indexOf("\n", start);
    const end = next < 0 ? text.length : next;
    const closing = FENCE_CLOSE.exec(text.slice(start, end))?.[1] ?? "";
    if (closing.length >= marker.length && closing[0] === marker[0]) return Math.min(end + 1, text.length);
    newline = next;
  }
  return text.length;
}
