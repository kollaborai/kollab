import { expect, test } from "vitest";
import { gitStatusPath } from "../src/components/code/lazy-file-list";

test("Git status keys use slash separators on either daemon platform", () => {
  expect(gitStatusPath("/work/repo", "/work/repo/src/file.ts")).toBe("src/file.ts");
  expect(gitStatusPath("C:\\work\\repo", "C:\\work\\repo\\src\\file.ts")).toBe("src/file.ts");
});
