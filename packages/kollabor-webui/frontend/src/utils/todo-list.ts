/**
 * The agent's task list. Agents write for the terminal (bundles/agents/_base/
 * sections/meta/terminal-formatting.md): a lowercase "todo:" label, then plain
 * "[x]" / "[ ]" lines. The Session Controls panel shows the latest list.
 *
 * Relative imports and installed packages only, no "@/" alias: node:test runs this file with its types stripped.
 */
import { fromMarkdown } from "mdast-util-from-markdown";

export type TodoItem = { done: boolean; text: string };

const LABEL = /^(\s*)todo:\s*(.*)$/i;
const ITEM = /^(\s*)\[([ xX])\]\s+(\S.*)$/;
// "assessment:", "next:": another label ends the wrapped text of an item.
const OTHER_LABEL = /^\s*[a-z][a-z ()/-]*:\s*$/i;

const indentOf = (line: string) => line.length - line.trimStart().length;

/** A node of the Markdown tree, as far as the code ranges need it. */
type MdNode = { type: string; children?: MdNode[]; position?: { start: { line: number }; end: { line: number } } };

// Only a text with a fence mark (three backticks or tildes) or a line indented by four columns (a tab counts as four)
// can hold a code block; every other text skips the parser.
const MAYBE_CODE = /```|~~~|^ *\t|^ {4}/m;

/**
 * The lines (0-based) the thread draws as code: fenced blocks, wherever a list item or a quote holds them, and
 * indented blocks. The text is read with the parser the thread renders through (CommonMark), so no container rule
 * is written a second time here. A fence that is never closed runs to the end of the text.
 */
function codeLines(text: string): Set<number> {
  const lines = new Set<number>();
  if (!MAYBE_CODE.test(text)) return lines;
  const pending: MdNode[] = [fromMarkdown(text)];
  for (let node = pending.pop(); node; node = pending.pop()) {
    if (node.type === "code" && node.position) {
      for (let line = node.position.start.line; line <= node.position.end.line; line += 1) lines.add(line - 1);
    } else if (node.children) {
      for (const child of node.children) pending.push(child);
    }
  }
  return lines;
}

// The latest list is looked up on every update of the thread, so a text that was read once is not read again.
const MEMO_LIMIT = 8;
const memo = new Map<string, string[]>();

/**
 * Every todo block of a text, in order, as written: the label line, then its
 * checkbox lines (a line indented deeper than its checkbox continues that
 * item). A blank line, any other line or a block of code ends it. "todo:" with
 * no checkbox line under it is prose, not a block, and a checklist under
 * another label is not a todo list. Nothing the thread draws as code counts
 * (see `codeLines`).
 */
export function todoBlocks(text: string): string[] {
  const known = memo.get(text);
  if (known) return known.slice();
  const blocks = readBlocks(text);
  if (memo.size >= MEMO_LIMIT) memo.delete(memo.keys().next().value as string);
  memo.set(text, blocks);
  return blocks.slice();
}

function readBlocks(text: string): string[] {
  const source = text.replace(/\r\n?/g, "\n");
  const lines = source.split("\n");
  const code = codeLines(source);
  const blocks: string[] = [];
  for (let i = 0; i < lines.length; i += 1) {
    if (code.has(i)) continue;
    const line = lines[i];
    const label = LABEL.exec(line);
    if (!label) continue;
    // "todo: [x] item" is the one-line form; "todo: finish the report" is prose.
    if (label[2] && !ITEM.test(label[2])) continue;
    const block = [line];
    let count = label[2] ? 1 : 0;
    let indent = label[1].length;
    let end = i + 1;
    for (; end < lines.length; end += 1) {
      const next = lines[end];
      if (code.has(end)) break;
      if (!next.trim()) {
        if (count === 0) continue; // a blank line before the first item is tolerated
        break;
      }
      const item = ITEM.exec(next);
      if (item) {
        block.push(next);
        count += 1;
        indent = item[1].length;
        continue;
      }
      if (count > 0 && indentOf(next) > indent && !OTHER_LABEL.test(next)) {
        block.push(next);
        continue;
      }
      break;
    }
    if (count > 0) {
      blocks.push(block.join("\n"));
      i = end - 1;
    }
  }
  return blocks;
}

/** The items of one block from `todoBlocks`. */
export function todoItems(block: string): TodoItem[] {
  const lines = block.split(/\r?\n/);
  const label = LABEL.exec(lines[0] ?? "");
  const rest = label?.[2] ? [label[2], ...lines.slice(1)] : lines.slice(1);
  const items: TodoItem[] = [];
  for (const line of rest) {
    const item = ITEM.exec(line);
    if (item) items.push({ done: item[2] !== " ", text: item[3].trim() });
    else if (items.length && line.trim()) items[items.length - 1].text += ` ${line.trim()}`;
  }
  return items;
}

/** What a thread message offers: its role, its text parts and the custom metadata the runtime puts on hub notes. */
export type TodoMessage = {
  role: string;
  parts: ReadonlyArray<{ type: string; text?: string }>;
  metadata?: { custom?: Record<string, unknown> };
};

/**
 * The agent's current list: the last block of the newest text of its own that
 * holds one, as written, or "" when there is none. A person's message and a
 * hub note (another agent's text) never count. A string, so a store selector
 * can return it without a new object per update.
 */
export function latestTodoBlock(messages: readonly TodoMessage[]): string {
  for (let m = messages.length - 1; m >= 0; m -= 1) {
    const message = messages[m];
    if (message.role !== "assistant" || message.metadata?.custom?.hub) continue;
    for (let p = message.parts.length - 1; p >= 0; p -= 1) {
      const part = message.parts[p];
      if (part.type !== "text" || !part.text || !/todo:/i.test(part.text)) continue;
      const blocks = todoBlocks(part.text);
      if (blocks.length) return blocks[blocks.length - 1];
    }
  }
  return "";
}
