export type TaggedTextPart = { type: "text"; text: string } | { type: "tag"; name: string; content: string };

type Open = { name: string; close: number };

const TAG = /<(\/?)([a-z][\w-]*)>/gi;
const FENCE_OPEN = / {0,3}(`{3,}|~{3,})/y;
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})\s*$/;
const INDENTED = /(?: {4}| {0,3}\t)/y;

/** Split only registered, complete tags. Tag content is returned raw; code spans, fences and indented blocks stay text. */
export function splitPluginTags(text: string, names: readonly string[]): TaggedTextPart[] {
  const allowed = new Set(names.filter((name) => /^[a-z][\w-]*$/i.test(name)));
  const opens = pairTags(text, allowed);
  const parts: TaggedTextPart[] = [];
  let cursor = 0;
  let index = 0;
  while (index < text.length) {
    if (index === 0 || text[index - 1] === "\n") {
      const blockEnd = codeBlockEnd(text, index);
      if (blockEnd >= 0) {
        index = blockEnd;
        continue;
      }
    }
    if (text[index] === "\\") { index += 2; continue; }
    if (text[index] === "`") {
      let end = index;
      while (text[end] === "`") end++;
      const delimiter = text.slice(index, end);
      const close = text.indexOf(delimiter, end);
      index = close < 0 ? end : close + delimiter.length;
      continue;
    }
    const open = opens.get(index);
    if (!open) { index++; continue; }
    if (open.close < 0) { index += open.name.length + 2; continue; }
    if (cursor < index) parts.push({ type: "text", text: text.slice(cursor, index) });
    parts.push({ type: "tag", name: open.name, content: text.slice(index + open.name.length + 2, open.close) });
    index = open.close + open.name.length + 3;
    cursor = index;
  }
  if (cursor < text.length || !parts.length) parts.push({ type: "text", text: text.slice(cursor) });
  return parts;
}

/** Pair open tags with closes in one pass: a close takes the nearest pending open of its name. */
function pairTags(text: string, allowed: ReadonlySet<string>): Map<number, Open> {
  const opens = new Map<number, Open>();
  const pending = new Map<string, Open[]>();
  for (const match of text.matchAll(TAG)) {
    const [, slash, name] = match;
    if (!name || !allowed.has(name)) continue;
    if (slash) {
      const open = pending.get(name)?.pop();
      if (open) open.close = match.index ?? 0;
      continue;
    }
    const open: Open = { name, close: -1 };
    opens.set(match.index ?? 0, open);
    const stack = pending.get(name);
    if (stack) stack.push(open);
    else pending.set(name, [open]);
  }
  return opens;
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
