import { beforeEach, expect, test, vi } from "vitest";

const codeFetch = vi.fn();
vi.mock("../src/lib/code-fetch", () => ({
  codeFetch: (...args: unknown[]) => codeFetch(...args),
  unwrapCodeData: (raw: { data: unknown }) => raw.data,
}));

import { isDirty, useEditorStore } from "../src/components/code/editor-store";
import { openWorkspaceFile } from "../src/components/code/open-file";

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
