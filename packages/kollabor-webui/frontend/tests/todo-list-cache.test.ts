// Run: npx vitest run tests/todo-list-cache.test.ts
// The todo card looks up the agent's latest list on every update of the thread (`App.tsx`), newest text first. The
// Markdown parser is what a lookup costs, so these tests count its calls: a thread that did not change must not be
// read again, however many texts the lookup has to visit before it finds a list or gives up.
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("mdast-util-from-markdown", async (importOriginal) => {
  const real = await importOriginal<typeof import("mdast-util-from-markdown")>();
  return { ...real, fromMarkdown: vi.fn(real.fromMarkdown) };
});

import { fromMarkdown } from "mdast-util-from-markdown";
import { latestTodoBlock, type TodoMessage } from "../src/utils/todo-list";

const parsed = vi.mocked(fromMarkdown);

const LIST = "todo:\n  [x] one\n  [ ] two";

/** An assistant message that shows how a list is written, in a fence: it says "todo:" and offers no list. */
const example = (salt: string, n: number): TodoMessage => ({
  role: "assistant",
  parts: [{ type: "text", text: `Example ${salt}-${n}:\n\n\`\`\`\ntodo:\n  [ ] example ${n}\n\`\`\`` }],
});
const examples = (salt: string, count: number): TodoMessage[] =>
  Array.from({ length: count }, (_, n) => example(salt, n));
const said = (text: string): TodoMessage => ({ role: "assistant", parts: [{ type: "text", text }] });

// The cache lives in the module, so every test uses texts of its own (`salt`) and starts by counting from zero.
beforeEach(() => parsed.mockClear());

describe("a lookup over more texts than any cache of a few entries could hold", () => {
  it("reads each text once while the thread does not change", () => {
    const thread = examples("unchanged", 12);

    expect(latestTodoBlock(thread)).toBe("");
    expect(parsed).toHaveBeenCalledTimes(12); // the first lookup does read them, or this test proves nothing

    parsed.mockClear();
    expect(latestTodoBlock(thread)).toBe("");
    expect(latestTodoBlock(examples("unchanged", 12))).toBe(""); // a rebuilt thread with the same texts
    expect(parsed).not.toHaveBeenCalled();
  });

  it("reads only the text that is new when the thread grows or a text is edited", () => {
    const thread = examples("grows", 10);
    latestTodoBlock(thread);

    parsed.mockClear();
    const grown = [...thread, example("grows", 10)];
    latestTodoBlock(grown);
    expect(parsed).toHaveBeenCalledTimes(1);

    parsed.mockClear();
    latestTodoBlock([...thread, said("Edited:\n\n```\ntodo:\n  [x] edited\n```")]);
    expect(parsed).toHaveBeenCalledTimes(1);
  });

  it("finds a list under many newer examples and does not read them again", () => {
    const thread = [said(LIST), ...examples("after-list", 10)];

    expect(latestTodoBlock(thread)).toBe(LIST);

    parsed.mockClear();
    expect(latestTodoBlock(thread)).toBe(LIST);
    expect(parsed).not.toHaveBeenCalled();
  });

  it("answers a thread it has not seen from that thread, not from the one before", () => {
    const first = [said(LIST), ...examples("first-thread", 10)];
    const second = [said("todo:\n  [ ] other task"), ...examples("second-thread", 3)];

    expect(latestTodoBlock(first)).toBe(LIST);
    expect(latestTodoBlock(second)).toBe("todo:\n  [ ] other task");
    expect(latestTodoBlock(first)).toBe(LIST);
  });
});
