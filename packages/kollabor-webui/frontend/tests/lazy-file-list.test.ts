import { expect, test } from "vitest";
import { gitStatusPath } from "../src/components/code/lazy-file-list";
import { scanQuickFiles, type DirectoryPage } from "../src/components/code/paged-quick-open";

test("Git status keys use slash separators on either daemon platform", () => {
  expect(gitStatusPath("/work/repo", "/work/repo/src/file.ts")).toBe("src/file.ts");
  expect(gitStatusPath("C:\\work\\repo", "C:\\work\\repo\\src\\file.ts")).toBe("src/file.ts");
});

test("quick open searches later pages and nested folders", async () => {
  const pages: Record<string, DirectoryPage> = {
    "/work:": { entries: [{ name: "sub", path: "/work/sub", type: "dir" }], next: "1:a" },
    "/work:1:a": { entries: [{ name: "target.ts", path: "/work/target.ts", type: "file", ext: ".ts" }], next: null },
    "/work/sub:": { entries: [{ name: "target.py", path: "/work/sub/target.py", type: "file", ext: ".py" }], next: null },
  };
  const result = await scanQuickFiles("/work", "target", async (path, after) => pages[`${path}:${after ?? ""}`], () => false);
  expect(result.files.map((file) => file.relativePath)).toEqual(["sub/target.py", "target.ts"]);
  expect(result.partial).toBe(false);
});

test("quick open stops reading after cancellation", async () => {
  let cancelled = false;
  let calls = 0;
  const result = await scanQuickFiles("/work", "", async () => {
    calls += 1;
    cancelled = true;
    return { entries: [{ name: "a", path: "/work/a", type: "file" }], next: "1:a" };
  }, () => cancelled);
  expect(calls).toBe(1);
  expect(result.files).toEqual([]);
});
