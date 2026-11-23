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
});
