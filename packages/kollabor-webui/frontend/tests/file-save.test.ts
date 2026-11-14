import { afterEach, expect, test, vi } from "vitest";

const fetchMock = vi.fn();
vi.mock("../src/lib/code-fetch", () => ({
  codeFetch: (...args: unknown[]) => fetchMock(...args),
  unwrapCodeData: <T,>(payload: unknown): T => {
    const record = payload as { data?: T };
    return (record.data ?? payload) as T;
  },
}));

import { forgetMtime, mtimeOf, rememberMtime, rememberVersion, saveWorkspaceFile } from "../src/components/code/file-save";

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  fetchMock.mockReset();
  forgetMtime("/w/a.txt");
});

test("virtual commit views cannot be sent to the file endpoint", async () => {
  const result = await saveWorkspaceFile("/w", "\0commit:abcd", "patch", { force: true });
  expect(result).toEqual({ ok: false, stale: false, message: "This view is read-only." });
  expect(fetchMock).not.toHaveBeenCalled();
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

test("a network failure reports an unknown save outcome", async () => {
  fetchMock.mockRejectedValue(new Error("down"));
  expect(await saveWorkspaceFile("/w", "/w/a.txt", "x")).toEqual({
    ok: false, stale: false, message: "The save result is unknown; check the file before retrying.",
  });
});

test("a daemon failure warns that the save may have completed", async () => {
  fetchMock.mockResolvedValue(reply(502, { success: false, error: { code: "daemon_unreachable", message: "No reply." } }));
  const result = await saveWorkspaceFile("/w", "/w/a.txt", "x");
  expect(result.ok).toBe(false);
  if (!result.ok) expect(result.message).toContain("save may have completed");
});


test("a version token takes precedence over the coarse mtime", async () => {
  rememberMtime("/w/a.txt", 1000);
  rememberVersion("/w/a.txt", "revision-one");
  fetchMock.mockResolvedValue(reply(200, { success: true, data: { version: "revision-two" } }));
  await saveWorkspaceFile("/w", "/w/a.txt", "x");
  const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
  expect(body.expectedVersion).toBe("revision-one");
  expect(body.expectedMtime).toBeUndefined();
});

test("a delayed save cannot update the version after the owner changes", async () => {
  const { setEditorOwner } = await import("../src/components/code/workspace-tabs");
  setEditorOwner("device-a:/w");
  rememberVersion("/w/a.txt", "a-version");
  let finish!: (response: Response) => void;
  fetchMock.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const saving = saveWorkspaceFile("/w", "/w/a.txt", "a");
  setEditorOwner("device-b:/w");
  rememberVersion("/w/a.txt", "b-version");
  finish(reply(200, { success: true, data: { version: "a-after" } }));
  expect((await saving).ok).toBe(false);
  fetchMock.mockResolvedValue(reply(200, { success: true, data: {} }));
  await saveWorkspaceFile("/w", "/w/a.txt", "b");
  const body = JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string);
  expect(body.expectedVersion).toBe("b-version");
});

test("a delayed save cannot update a version after switching away and back", async () => {
  const { setEditorOwner } = await import("../src/components/code/workspace-tabs");
  setEditorOwner("device-a:/w");
  rememberVersion("/w/a.txt", "before");
  let finish!: (response: Response) => void;
  fetchMock.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const saving = saveWorkspaceFile("/w", "/w/a.txt", "a");
  setEditorOwner("device-b:/w");
  setEditorOwner("device-a:/w");
  rememberVersion("/w/a.txt", "newer");
  finish(reply(200, { success: true, data: { version: "old-response" } }));
  expect((await saving).ok).toBe(false);
  fetchMock.mockResolvedValue(reply(200, { success: true, data: {} }));
  await saveWorkspaceFile("/w", "/w/a.txt", "b");
  const body = JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string);
  expect(body.expectedVersion).toBe("newer");
});

test("a delayed save cannot cross sessions sharing a folder", async () => {
  const { setEditorOwner } = await import("../src/components/code/workspace-tabs");
  setEditorOwner("device-a:/w", "session-a");
  let finish!: (response: Response) => void;
  fetchMock.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const saving = saveWorkspaceFile("/w", "/w/a.txt", "a");
  setEditorOwner("device-a:/w", "session-b");
  setEditorOwner("device-a:/w", "session-a");
  finish(reply(200, { success: true, data: { version: "old-session" } }));
  expect((await saving).ok).toBe(false);
});

test("overlapping saves of the same path do not issue a second write", async () => {
  let finish!: (response: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const first = saveWorkspaceFile("/w", "/w/a.txt", "first");
  const second = await saveWorkspaceFile("/w", "/w/a.txt", "second");
  expect(second).toEqual({ ok: false, stale: false, message: "Another save for this file is in progress." });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  finish(reply(200, { success: true, data: {} }));
  expect(await first).toEqual({ ok: true });
  fetchMock.mockResolvedValue(reply(200, { success: true, data: {} }));
  expect(await saveWorkspaceFile("/w", "/w/a.txt", "after")).toEqual({ ok: true });
});

test("different paths can save concurrently and failures release the guard", async () => {
  let finish!: (response: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  fetchMock.mockResolvedValueOnce(reply(200, { success: true, data: {} }));
  const first = saveWorkspaceFile("/w", "/w/a.txt", "first");
  expect(await saveWorkspaceFile("/w", "/w/b.txt", "other")).toEqual({ ok: true });
  finish(reply(403, { success: false, error: { message: "Read-only." } }));
  expect((await first).ok).toBe(false);
  fetchMock.mockResolvedValueOnce(reply(200, { success: true, data: {} }));
  expect(await saveWorkspaceFile("/w", "/w/a.txt", "retry")).toEqual({ ok: true });
});

test("a failed request releases the pending-save guard", async () => {
  fetchMock.mockRejectedValueOnce(new Error("network"));
  expect((await saveWorkspaceFile("/w", "/w/a.txt", "first")).ok).toBe(false);
  fetchMock.mockResolvedValueOnce(reply(200, { success: true, data: {} }));
  expect(await saveWorkspaceFile("/w", "/w/a.txt", "retry")).toEqual({ ok: true });
});
