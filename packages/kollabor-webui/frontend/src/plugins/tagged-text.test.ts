import { describe, expect, it } from "vitest";
import { splitPluginTags } from "./tagged-text";

describe("plugin tagged text", () => {
  it("extracts only registered complete tags without interpreting their contents", () => {
    expect(splitPluginTags('before <todo_write><script>alert(1)</script></todo_write> after', ["todo_write"])).toEqual([
      { type: "text", text: "before " },
      { type: "tag", name: "todo_write", content: "<script>alert(1)</script>" },
      { type: "text", text: " after" },
    ]);
  });
  it("leaves unknown, incomplete, escaped and code-fenced tags alone", () => {
    const text = "<unknown>x</unknown> \\<todo_write>escaped</todo_write>\n`<todo_write>inline</todo_write>`\n```\n<todo_write>fenced</todo_write>\n```\n<todo_write>partial";
    expect(splitPluginTags(text, ["todo_write"])).toEqual([{ type: "text", text }]);
  });
  it("does not interpret tags inside indented Markdown code blocks", () => {
    const text = "Example:\n\n    <todo_write>four spaces</todo_write>\n\t<todo_write>tab</todo_write>\n\nAfter <todo_write>real</todo_write>";
    expect(splitPluginTags(text, ["todo_write"])).toEqual([
      { type: "text", text: "Example:\n\n    <todo_write>four spaces</todo_write>\n\t<todo_write>tab</todo_write>\n\nAfter " },
      { type: "tag", name: "todo_write", content: "real" },
    ]);
  });
  it("handles nested matching tags and multiple independent blocks", () => {
    expect(splitPluginTags("<x>a<x>b</x>c</x> + <x>d</x>", ["x"])).toEqual([
      { type: "tag", name: "x", content: "a<x>b</x>c" }, { type: "text", text: " + " }, { type: "tag", name: "x", content: "d" },
    ]);
  });
  it("keeps a backtick run with no closer as text, so later tags still split", () => {
    expect(splitPluginTags("a ` then <todo_write>demo</todo_write>", ["todo_write"])).toEqual([
      { type: "text", text: "a ` then " },
      { type: "tag", name: "todo_write", content: "demo" },
    ]);
  });
  it("closes a code fence only on a bare marker line", () => {
    const text = "~~~\n~~~text\n<todo_write>demo</todo_write>\n~~~";
    expect(splitPluginTags(text, ["todo_write"])).toEqual([{ type: "text", text }]);
  });
  it("pairs many unclosed opens with one close in linear time", () => {
    const started = performance.now();
    const parts = splitPluginTags("<x>".repeat(2000) + "</x>", ["x"]);
    expect(performance.now() - started).toBeLessThan(500);
    expect(parts).toEqual([
      { type: "text", text: "<x>".repeat(1999) },
      { type: "tag", name: "x", content: "" },
    ]);
  });
  it("does not let a close inside a code span, escape or fence end a real tag", () => {
    expect(splitPluginTags("<todo_write>a `</todo_write>` b</todo_write>", ["todo_write"])).toEqual([
      { type: "tag", name: "todo_write", content: "a `</todo_write>` b" },
    ]);
    expect(splitPluginTags("<todo_write>a \\</todo_write> b</todo_write>", ["todo_write"])).toEqual([
      { type: "tag", name: "todo_write", content: "a \\</todo_write> b" },
    ]);
    expect(splitPluginTags("<todo_write>a \\<todo_write>b</todo_write>", ["todo_write"])).toEqual([
      { type: "tag", name: "todo_write", content: "a \\<todo_write>b" },
    ]);
    const fenced = "<todo_write>a\n```\n</todo_write>\n```";
    expect(splitPluginTags(fenced, ["todo_write"])).toEqual([{ type: "text", text: fenced }]);
  });
  it("closes a code span only on a backtick run of the same length", () => {
    const text = "`a ```<todo_write>x</todo_write>`";
    expect(splitPluginTags(text, ["todo_write"])).toEqual([{ type: "text", text }]);
  });
});
