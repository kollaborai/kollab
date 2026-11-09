import { afterEach, expect, test, vi } from "vitest";

const fetchMock = vi.fn();
vi.mock("../src/lib/code-fetch", () => ({
  codeFetch: (...args: unknown[]) => fetchMock(...args),
  unwrapCodeData: <T,>(payload: unknown): T => {
    const record = payload as { data?: T };
    return (record.data ?? payload) as T;
  },
}));

import { forgetMtime, mtimeOf, rememberMtime, saveWorkspaceFile } from "../src/components/code/file-save";

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  fetchMock.mockReset();
  forgetMtime("/w/a.txt");
});

test("a save sends the mtime the file was read with", async () => {
  rememberMtime("/w/a.txt", 1000);
  fetchMock.mockResolvedValue(reply(200, { success: true, data: { mtime: 2000 } }));
  const result = await saveWorkspaceFile("/w", "/w/a.txt", "x");
  expect(result).toEqual({ ok: true });
  const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
  expect(body).toMatchObject({ workspace: "/w", content: "x", expectedMtime: 1000 });
  expect(body.force).toBeUndefined();
  expect(mtimeOf("/w/a.txt")).toBe(2000);
});

test("a file never read sends no expectedMtime", async () => {
  fetchMock.mockResolvedValue(reply(200, { success: true, data: {} }));
  await saveWorkspaceFile("/w", "/w/a.txt", "x");
  const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
  expect("expectedMtime" in body).toBe(false);
});

test("a stale write answers stale and keeps the old mtime", async () => {
  rememberMtime("/w/a.txt", 1000);
  fetchMock.mockResolvedValue(
    reply(409, { success: false, error: { code: "stale_write", message: "The file changed on disk since you opened it." } }),
  );
  const result = await saveWorkspaceFile("/w", "/w/a.txt", "x");
  expect(result).toEqual({ ok: false, stale: true, message: "The file changed on disk since you opened it." });
  expect(mtimeOf("/w/a.txt")).toBe(1000);
});

test("force overwrites and sends force true", async () => {
  rememberMtime("/w/a.txt", 1000);
  fetchMock.mockResolvedValue(reply(200, { success: true, data: { mtime: 3000 } }));
  await saveWorkspaceFile("/w", "/w/a.txt", "x", { force: true });
  const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
  expect(body.force).toBe(true);
  expect(mtimeOf("/w/a.txt")).toBe(3000);
});

test("another failure is not stale", async () => {
  fetchMock.mockResolvedValue(reply(403, { success: false, error: { code: "read_only", message: "Read-only. Setting: kollabor.code.read_only" } }));
  const result = await saveWorkspaceFile("/w", "/w/a.txt", "x");
  expect(result).toEqual({ ok: false, stale: false, message: "Read-only. Setting: kollabor.code.read_only" });
});

test("a network failure says save failed", async () => {
  fetchMock.mockRejectedValue(new Error("down"));
  expect(await saveWorkspaceFile("/w", "/w/a.txt", "x")).toEqual({ ok: false, stale: false, message: "save failed" });
});
