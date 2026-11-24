export type TaggedTextPart = { type: "text"; text: string } | { type: "tag"; name: string; content: string };

type Open = { name: string; close: number };
type Mask = { start: number; end: number };
type BacktickRun = { start: number; end: number; length: number };

const TAG = /<(\/?)([a-z][\w-]*)>/gi;
const CONTAINER = /(?: {0,3}(?:>[ \t]?|(?:[-*+]|\d{1,9}[.)])[ \t]+))*/y;
const FENCE_OPEN = / {0,3}(`{3,}|~{3,})/y;
const FENCE_CLOSE = /^((?: {0,3}>)*) {0,3}(`{3,}|~{3,})\s*$/;
const ATX_HEADING = / {0,3}#{1,6}(?:[ \t]|\r?\n|$)/y;
const THEMATIC_BREAK = / {0,3}(?:\*(?:[ \t]*\*){2,}|-(?:[ \t]*-){2,}|_(?:[ \t]*_){2,})[ \t]*\r?(?:\n|$)/y;
const BLOCKQUOTE = / {0,3}>[ \t]?/y;
const BLANK_LINE = /\n(?:[ \t]*>)*[ \t\r]*\n/g;
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
  const codeSpanClosers = indexCodeSpanClosers(text);
  let index = 0;
  let previousIndentedCode = false;
  while (index < text.length) {
    if (index === 0 || text[index - 1] === "\n") {
      if (isBlankLineAt(text, index)) {
        index = lineEnd(text, index);
        continue;
      }
      const indentedCode = startsIndentedCode(text, index, previousIndentedCode);
      const blockEnd = codeBlockEnd(text, index, indentedCode);
      if (blockEnd >= 0) {
        masked.push({ start: index, end: blockEnd });
        previousIndentedCode = indentedCode;
        index = blockEnd;
        continue;
      }
      previousIndentedCode = false;
    }
    if (text[index] === "\\") {
      masked.push({ start: index, end: index + 2 });
      index += 2;
      continue;
    }
    if (text[index] === "`") {
      const end = codeSpanEnd(text, index, codeSpanClosers);
      masked.push({ start: index, end });
      index = end;
      continue;
    }
    index++;
  }
  return masked;
}

/** Pair each backtick run with the next run of the same length in the same Markdown block. */
function indexCodeSpanClosers(text: string): ReadonlyMap<number, number> {
  const runs: BacktickRun[] = [];
  const blockBoundaries: number[] = [];
  const listItems: number[] = [];
  let previousIndentedCode = false;
  for (let index = 0; index < text.length; ) {
    if (index === 0 || text[index - 1] === "\n") {
      if (!isBlankLineAt(text, index)) {
        const indentedCode = startsIndentedCode(text, index, previousIndentedCode);
        const blockEnd = codeBlockEnd(text, index, indentedCode);
        if (blockEnd >= 0) {
          blockBoundaries.push(index);
          previousIndentedCode = indentedCode;
          index = blockEnd;
          continue;
        }
        previousIndentedCode = false;
        if (startsListItem(text, index)) listItems.push(index);
        if (startsMarkdownBlock(text, index)) blockBoundaries.push(index);
      }
    }
    if (text[index] !== "`") {
      index++;
      continue;
    }
    const start = index;
    while (text[index] === "`") index++;
    runs.push({ start, end: index, length: index - start });
  }

  const closers = new Map<number, number>();
  const pending = new Map<number, BacktickRun>();
  const blankLines = text.matchAll(BLANK_LINE);
  let blank = blankLines.next();
  let block = 0;
  let list = 0;
  for (const run of runs) {
    while (!blank.done && blank.value.index < run.start) {
      pending.clear();
      blank = blankLines.next();
    }
    while (block < blockBoundaries.length && blockBoundaries[block] <= run.start) {
      pending.clear();
      block++;
    }
    while (list < listItems.length && listItems[list] <= run.start) {
      pending.clear();
      list++;
    }
    const opener = pending.get(run.length);
    if (opener) closers.set(opener.start, run.end);
    pending.set(run.length, run);
  }
  return closers;
}

/** Block markers end the paragraph, so an unmatched inline-code opener cannot hide later tags. */
function startsMarkdownBlock(text: string, index: number): boolean {
  ATX_HEADING.lastIndex = index;
  if (ATX_HEADING.test(text)) return true;
  THEMATIC_BREAK.lastIndex = index;
  if (THEMATIC_BREAK.test(text)) return true;
  BLOCKQUOTE.lastIndex = index;
  return BLOCKQUOTE.test(text);
}

/** Index just past the code span that opens at this backtick run. The closer is a run of exactly the same length in the same paragraph; with none, the opening run is literal text. */
function codeSpanEnd(text: string, index: number, closers: ReadonlyMap<number, number>): number {
  let runEnd = index;
  while (text[runEnd] === "`") runEnd++;
  return closers.get(index) ?? runEnd;
}

/** Where the code block that starts at this line start ends, or -1 when none starts here. A fence may follow quote and list markers. */
function codeBlockEnd(
  text: string,
  index: number,
  indentedCode = startsIndentedCode(text, index, false),
): number {
  if (indentedCode) return lineEnd(text, index);
  CONTAINER.lastIndex = index;
  CONTAINER.exec(text);
  const prefix = text.slice(index, CONTAINER.lastIndex);
  FENCE_OPEN.lastIndex = CONTAINER.lastIndex;
  const marker = FENCE_OPEN.exec(text)?.[1];
  if (!marker) return -1;
  // A backtick fence's info string cannot hold a backtick: such a line is an inline code span, not a fence.
  if (marker[0] === "`" && text.slice(FENCE_OPEN.lastIndex, lineEnd(text, index)).includes("`")) return -1;
  return fenceEnd(text, index, marker, quoteDepth(prefix));
}

/** Number of block quote markers in a line prefix. */
function quoteDepth(prefix: string): number {
  return prefix.split(">").length - 1;
}

/** Index just past the newline that ends the line holding `index`, or the text end. */
function lineEnd(text: string, index: number): number {
  const newline = text.indexOf("\n", index);
  return newline < 0 ? text.length : newline + 1;
}

/** Whether this physical line begins a new indented code block or continues one. */
function startsIndentedCode(text: string, index: number, previousIndentedCode: boolean): boolean {
  INDENTED.lastIndex = index;
  if (!INDENTED.test(text) || isBlankLineAt(text, index) || startsNestedListItem(text, index)) return false;
  if (previousIndentedCode || index === 0) return true;
  const previousLineEnd = index - 1;
  const previousLineStart = text.lastIndexOf("\n", previousLineEnd - 1) + 1;
  return text.slice(previousLineStart, previousLineEnd).trim() === "";
}

/** A four-space list marker continues a surrounding list instead of opening indented code. */
function startsNestedListItem(text: string, index: number): boolean {
  const currentLine = text.slice(index, lineEnd(text, index));
  const current = /^( {4,})(?:[-+*]|\d{1,9}[.)])[ \t]+/.exec(currentLine);
  if (!current) return false;
  const currentIndent = current[1].length;

  let previousLineEnd = index - 1;
  while (previousLineEnd >= 0) {
    const previousLineStart = text.lastIndexOf("\n", previousLineEnd - 1) + 1;
    const previousLine = text.slice(previousLineStart, previousLineEnd);
    if (previousLine.trim()) {
      const parent = /^( *)(?:[-+*]|\d{1,9}[.)])[ \t]+/.exec(previousLine);
      if (!parent) return false;
      const parentIndent = parent[1].length;
      return currentIndent === parentIndent ||
        (currentIndent >= parentIndent + 2 && currentIndent <= parentIndent + 4);
    }
    previousLineEnd = previousLineStart - 1;
  }
  return false;
}

/** A list item starts a new block, so inline code cannot continue into it. */
function startsListItem(text: string, index: number): boolean {
  CONTAINER.lastIndex = index;
  CONTAINER.exec(text);
  const prefix = text.slice(index, CONTAINER.lastIndex);
  return /(?:[-*+]|\d{1,9}[.)])[ \t]+/.test(prefix) || startsNestedListItem(text, index);
}

/** Whether this line contains only whitespace. */
function isBlankLineAt(text: string, index: number): boolean {
  return text.slice(index, lineEnd(text, index)).trim() === "";
}

/** Index just past the closing fence, or the text end when the fence never closes. The closer must sit at the opener's quote depth. */
function fenceEnd(text: string, index: number, marker: string, depth: number): number {
  let newline = text.indexOf("\n", index);
  while (newline >= 0) {
    const start = newline + 1;
    const next = text.indexOf("\n", start);
    const end = next < 0 ? text.length : next;
    const closing = FENCE_CLOSE.exec(text.slice(start, end));
    if (closing && quoteDepth(closing[1]) === depth && closing[2].length >= marker.length && closing[2][0] === marker[0]) {
      return Math.min(end + 1, text.length);
    }
    newline = next;
  }
  return text.length;
}
