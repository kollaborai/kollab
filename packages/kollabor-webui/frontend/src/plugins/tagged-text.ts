export type TaggedTextPart = { type: "text"; text: string } | { type: "tag"; name: string; content: string };

/** Split only registered, complete tags. Code and untrusted tag content stay plain text. */
export function splitPluginTags(text: string, names: readonly string[]): TaggedTextPart[] {
  const allowed = new Set(names.filter(name => /^[a-z][\w-]*$/i.test(name)));
  const parts: TaggedTextPart[] = [];
  let cursor = 0;
  let index = 0;
  let fence: { char: string; length: number } | null = null;
  while (index < text.length) {
    if (index === 0 || text[index - 1] === "\n") {
      const lineEnd = text.indexOf("\n", index);
      const end = lineEnd < 0 ? text.length : lineEnd;
      const line = text.slice(index, end);
      const marker = line.match(/^ {0,3}(`{3,}|~{3,})/);
      if (marker && (!fence || (marker[1][0] === fence.char && marker[1].length >= fence.length))) {
        fence = fence ? null : { char: marker[1][0], length: marker[1].length };
        index = lineEnd < 0 ? text.length : lineEnd + 1;
        continue;
      }
      if (fence || /^(?: {4}| {0,3}\t)/.test(line)) {
        index = lineEnd < 0 ? text.length : lineEnd + 1;
        continue;
      }
    }
    if (text[index] === "\\") { index += 2; continue; }
    if (text[index] === "`") {
      let end = index;
      while (text[end] === "`") end++;
      const delimiter = text.slice(index, end);
      const close = text.indexOf(delimiter, end);
      index = close < 0 ? text.length : close + delimiter.length;
      continue;
    }
    if (text[index] !== "<") { index++; continue; }
    const match = text.slice(index).match(/^<([a-z][\w-]*)>/i);
    if (!match || !allowed.has(match[1])) { index++; continue; }
    const name = match[1];
    const open = `<${name}>`;
    const close = `</${name}>`;
    let depth = 1;
    let scan = index + open.length;
    let closeIndex = -1;
    while (depth > 0) {
      const nextOpen = text.indexOf(open, scan);
      const nextClose = text.indexOf(close, scan);
      if (nextClose < 0) break;
      if (nextOpen >= 0 && nextOpen < nextClose) { depth++; scan = nextOpen + open.length; }
      else { depth--; closeIndex = nextClose; scan = nextClose + close.length; }
    }
    if (depth !== 0) { index += open.length; continue; }
    if (cursor < index) parts.push({ type: "text", text: text.slice(cursor, index) });
    parts.push({ type: "tag", name, content: text.slice(index + open.length, closeIndex) });
    cursor = scan;
    index = scan;
  }
  if (cursor < text.length || !parts.length) parts.push({ type: "text", text: text.slice(cursor) });
  return parts;
}
