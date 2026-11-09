import { expect, test } from "vitest";

import { codeBase, codeUrl, setCodeSession } from "../src/lib/code-fetch";

test("file and git calls go to the session's own code routes", () => {
  expect(codeUrl("/api/fs/tree?workspace=%2Fa", "s1")).toBe("/sessions/s1/code/fs/tree?workspace=%2Fa");
  expect(codeUrl("/api/git", "s1")).toBe("/sessions/s1/code/git");
  expect(codeUrl("/api/git?action=status", "s1")).toBe("/sessions/s1/code/git?action=status");
});

test("a session id is encoded so it cannot change the route", () => {
  expect(codeBase("a/b?c")).toBe("/sessions/a%2Fb%3Fc/code");
});

test("the session the open view set is used when none is passed", () => {
  setCodeSession("remote@laptop");
  expect(codeUrl("/api/fs/file?path=%2Fx")).toBe("/sessions/remote%40laptop/code/fs/file?path=%2Fx");
  setCodeSession("local-1");
  expect(codeUrl("/api/fs/file?path=%2Fx")).toBe("/sessions/local-1/code/fs/file?path=%2Fx");
});
