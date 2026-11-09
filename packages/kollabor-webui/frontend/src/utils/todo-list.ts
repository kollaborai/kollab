/**
 * The agent's task list. Agents write for the terminal (bundles/agents/_base/
 * sections/meta/terminal-formatting.md): a lowercase "todo:" label, then plain
 * "[x]" / "[ ]" lines. The Session Controls panel shows the latest list.
 *
 * Plain relative imports only: node:test runs this file with its types stripped.
 */
export type TodoItem = { done: boolean; text: string };

const LABEL = /^(\s*)todo:\s*(.*)$/i;
const ITEM = /^(\s*)\[([ xX])\]\s+(\S.*)$/;
// A code fence (CommonMark): three or more backticks or tildes, then an info string. A backtick fence's info string
// holds no backtick, or the line is inline code, not a fence.
const FENCE = /^\s*(`{3,}|~{3,})(.*)$/;
// "assessment:", "next:": another label ends the wrapped text of an item.
const OTHER_LABEL = /^\s*[a-z][a-z ()/-]*:\s*$/i;

const indentOf = (line: string) => line.length - line.trimStart().length;

type Fence = { char: string; length: number; closing: boolean };

/** The fence a line draws, or null. `closing` is true when nothing follows the marks, the only form that can close a fence. */
function fenceOf(line: string): Fence | null {
  const match = FENCE.exec(line);
  if (!match) return null;
  const [, marks, info] = match;
  if (marks[0] === "`" && info.includes("`")) return null;
  return { char: marks[0], length: marks.length, closing: !info.trim() };
}

/** A fence closes only on the same character, at least as many of it, and nothing after: the rest of the text is still code. */
const closes = (open: Fence, line: Fence) => line.closing && line.char === open.char && line.length >= open.length;

/**
 * Every todo block of a text, in order, as written: the label line, then its
 * checkbox lines (a line indented deeper than its checkbox continues that
 * item). A blank line, any other line or a code fence ends it. "todo:" with no
 * checkbox line under it is prose, not a block, and a checklist under another
 * label is not a todo list. Nothing inside a code fence counts, and a fence
 * that is never closed runs to the end of the text.
 */
export function todoBlocks(text: string): string[] {
  const lines = text.split(/\r?\n/);
  const blocks: string[] = [];
  let open: Fence | null = null;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const fence = fenceOf(line);
    if (open) {
      if (fence && closes(open, fence)) open = null;
      continue;
    }
    if (fence) {
      open = fence;
      continue;
    }
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
      if (fenceOf(next)) break;
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
