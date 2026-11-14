import { beforeEach, expect, test, vi } from "vitest";

const codeFetch = vi.fn();
vi.mock("../src/lib/code-fetch", () => ({
  codeFetch: (...args: unknown[]) => codeFetch(...args),
  unwrapCodeData: (raw: { data: unknown }) => raw.data,
}));

import { isDirty, useEditorStore } from "../src/components/code/editor-store";
import { openWorkspaceDiff, openWorkspaceFile, readFileContent } from "../src/components/code/open-file";
import { mtimeOf } from "../src/components/code/file-save";
import { setEditorOwner } from "../src/components/code/workspace-tabs";

const PANE = "pane-test";
const WS = "/work";

function reply(ok: boolean, content = "") {
  return { ok, json: async () => ({ data: { content } }) };
}

beforeEach(() => {
  codeFetch.mockReset();
  useEditorStore.setState({
    fileCache: new Map(),
    panes: [{ id: PANE, openPaths: [], activePath: null }],
    activePaneId: PANE,
    splitTree: { type: "leaf", paneId: PANE },
  });
});

test("a file the engine refuses to read leaves no tab behind", async () => {
  codeFetch.mockResolvedValue(reply(false));
  expect(await openWorkspaceFile(WS, "/work/big.json")).toBe(false);
  expect(useEditorStore.getState().panes[0]?.openPaths).toEqual([]);
});

test("a read that throws leaves no tab behind", async () => {
  codeFetch.mockRejectedValue(new Error("offline"));
  expect(await openWorkspaceFile(WS, "/work/a.ts")).toBe(false);
  expect(useEditorStore.getState().panes[0]?.openPaths).toEqual([]);
});

test("a read that works opens the tab with the file's text", async () => {
  codeFetch.mockResolvedValue(reply(true, "hello"));
  expect(await openWorkspaceFile(WS, "/work/a.ts", { pin: true })).toBe(true);
  const state = useEditorStore.getState();
  expect(state.panes[0]?.openPaths).toEqual(["/work/a.ts"]);
  expect(state.fileCache.get("/work/a.ts")?.content).toBe("hello");
  expect(state.fileCache.get("/work/a.ts")?.pinned).toBe(true);
});

test("opening a dirty file in a second pane shows the edited buffer and reads nothing", async () => {
  codeFetch.mockResolvedValue(reply(true, "disk"));
  await openWorkspaceFile(WS, "/work/a.ts");
  useEditorStore.getState().updateContent("/work/a.ts", "edited");
  useEditorStore.getState().splitRight(PANE);
  const second = useEditorStore.getState().activePaneId;
  codeFetch.mockClear();

  expect(await openWorkspaceFile(WS, "/work/a.ts", { paneId: second })).toBe(true);
  const file = useEditorStore.getState().fileCache.get("/work/a.ts");
  expect(file?.content).toBe("edited");
  expect(file ? isDirty(file) : false).toBe(true);
  expect(codeFetch).not.toHaveBeenCalled();
});

test("a delayed failed read cannot close a replacement buffer for the same path", async () => {
  let finish!: (value: ReturnType<typeof reply>) => void;
  codeFetch.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const opening = openWorkspaceFile(WS, "/work/a.ts");
  useEditorStore.getState().closeFile(PANE, "/work/a.ts");
  useEditorStore.getState().openFile(PANE, "/work/a.ts", "a.ts", "ts", "new buffer");
  useEditorStore.getState().updateContent("/work/a.ts", "new edits");
  finish(reply(false));
  await opening;
  expect(useEditorStore.getState().fileCache.get("/work/a.ts")?.content).toBe("new edits");
  expect(useEditorStore.getState().panes[0]?.openPaths).toContain("/work/a.ts");
});


test("a delayed diff read cannot replace a newly opened buffer's revision", async () => {
  let finish!: (value: unknown) => void;
  codeFetch.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const diff = openWorkspaceDiff(WS, "/work/race.ts", "head");
  codeFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ data: { content: "new", mtime: 200 } }) });
  await openWorkspaceFile(WS, "/work/race.ts");
  finish({ ok: true, json: async () => ({ data: { content: "old", mtime: 100 } }) });
  await diff;
  expect(mtimeOf("/work/race.ts")).toBe(200);
  expect(useEditorStore.getState().fileCache.get("/work/race.ts")?.content).toBe("new");
});

test("a read from before an away-and-back switch cannot populate the returned owner", async () => {
  setEditorOwner("a:/work");
  let finish!: (value: ReturnType<typeof reply>) => void;
  codeFetch.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const reading = readFileContent(WS, "/work/a.ts");
  setEditorOwner("b:/work");
  setEditorOwner("a:/work");
  finish(reply(true, "old content"));
  expect(await reading).toBeNull();
});

test("a read cannot cross sessions sharing the same device and folder", async () => {
  setEditorOwner("a:/work", "session-a");
  let finish!: (value: ReturnType<typeof reply>) => void;
  codeFetch.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const reading = readFileContent(WS, "/work/a.ts");
  setEditorOwner("a:/work", "session-b");
  setEditorOwner("a:/work", "session-a");
  finish(reply(true, "old session"));
  expect(await reading).toBeNull();
});

test("concurrent panes share one read and preserve its revision after an edit", async () => {
  setEditorOwner("race:/work");
  let finish!: (value: unknown) => void;
  codeFetch.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const first = openWorkspaceFile(WS, "/work/race-1011.ts");
  useEditorStore.setState((state) => ({ panes: [...state.panes, { id: "second-1011", openPaths: [], activePath: null }] }));
  const second = openWorkspaceFile(WS, "/work/race-1011.ts", { paneId: "second-1011" });
  expect(codeFetch).toHaveBeenCalledTimes(1);
  finish({ ok: true, json: async () => ({ data: { content: "base", version: "v1" } }) });
  expect(await first).toBe(true);
  expect(await second).toBe(true);
  useEditorStore.getState().updateContent("/work/race-1011.ts", "edited");
  codeFetch.mockResolvedValue({ ok: false, status: 409, json: async () => ({ error: { code: "stale_write" } }) });
  const { saveWorkspaceFile } = await import("../src/components/code/file-save");
  expect((await saveWorkspaceFile(WS, "/work/race-1011.ts", "edited")).ok).toBe(false);
  const request = codeFetch.mock.calls.at(-1)?.[1] as { body: string };
  expect(JSON.parse(request.body).expectedVersion).toBe("v1");
});
