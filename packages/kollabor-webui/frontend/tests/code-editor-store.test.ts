import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { EmptyEditorState } from "../src/components/code/empty-editor-state";
import { editorThemeColors } from "../src/components/code/editor-theme";
import { visibleSplitTree } from "../src/components/code/split-layout";
import {
  CODE_EDITOR_LAYOUT_KEY,
  isDiffTab,
  isDirty,
  leafPaneIds,
  parseSavedLayout,
  useEditorStore,
  type EditorPane,
  type FileData,
} from "../src/components/code/editor-store";

const PANE = "pane-test";
const store = () => useEditorStore.getState();

let storage: Map<string, string>;

// narrows away null/undefined, failing the test with a clear message instead
function must<T>(value: T | null | undefined, label: string): T {
  if (value === null || value === undefined) throw new Error(`${label} is missing`);
  return value;
}

function resetEditor(): void {
  const pane: EditorPane = { id: PANE, openPaths: [], activePath: null };
  useEditorStore.setState({
    fileCache: new Map(),
    panes: [pane],
    activePaneId: PANE,
    splitTree: { type: "leaf", paneId: PANE },
  });
}

beforeEach(() => {
  // a Map-backed localStorage, so no test touches a real one
  storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => {
      storage.set(key, value);
    },
    removeItem: (key: string) => {
      storage.delete(key);
    },
  });
  resetEditor();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function paneState(id: string): EditorPane {
  return must(store().panes.find((p) => p.id === id), `pane ${id}`);
}

function fileState(path: string): FileData {
  return must(store().fileCache.get(path), `file ${path}`);
}

test("commit views cannot collide with or edit a real file and are not restored as disk paths", () => {
  const real = "/w/.diffs/abcd.diff";
  const virtual = "\0commit:abcd1234";
  store().openFile(PANE, real, "abcd.diff", "diff", "real file");
  store().pinFile(real);
  store().openVirtualFile(PANE, virtual, "commit", "diff", "patch");
  store().updateContent(virtual, "edited patch");
  expect(fileState(real).content).toBe("real file");
  expect(fileState(virtual)).toMatchObject({ content: "patch", virtual: true });
  const layout = JSON.parse(must(storage.get(CODE_EDITOR_LAYOUT_KEY), "layout"));
  expect(layout.panes[0].openPaths).toEqual([real]);
  expect(layout.panes[0].activePath).toBeNull();
});

test("the editor uses the active light palette for its canvas and text", () => {
  const palette = editorThemeColors(false, {
    background: "#ffffff",
    foreground: "#252525",
  });

  expect(palette.base).toBe("vs");
  expect(palette.colors["editor.background"]).toBe("#ffffff");
  expect(palette.colors["editor.foreground"]).toBe("#252525");
});

test("a narrow editor keeps only the active pane while desktop keeps the split", () => {
  const split = {
    type: "split" as const,
    direction: "horizontal" as const,
    first: { type: "leaf" as const, paneId: "left" },
    second: {
      type: "split" as const,
      direction: "vertical" as const,
      first: { type: "leaf" as const, paneId: "top-right" },
      second: { type: "leaf" as const, paneId: "bottom-right" },
      sizePercent: 50,
    },
    sizePercent: 50,
  };

  expect(visibleSplitTree(split, "bottom-right", true)).toEqual({ type: "leaf", paneId: "bottom-right" });
  expect(visibleSplitTree(split, "bottom-right", false)).toBe(split);
});

// a pinned tab is never replaced by a later preview open
function openPinned(paneId: string, path: string): void {
  store().openFile(paneId, path, path, "ts", `// ${path}`);
  store().pinFile(path);
}

test("opening the same file twice does not duplicate its tab", () => {
  store().openFile(PANE, "src/a.ts", "a.ts", "ts", "one");
  store().openFile(PANE, "src/a.ts", "a.ts", "ts", "one");
  expect(paneState(PANE).openPaths).toEqual(["src/a.ts"]);
  expect(paneState(PANE).activePath).toBe("src/a.ts");
});

test("closing a tab activates a neighbour: the left one, or the right one at the start", () => {
  openPinned(PANE, "a.ts");
  openPinned(PANE, "b.ts");
  openPinned(PANE, "c.ts");
  store().setActiveFile(PANE, "b.ts");

  store().closeFile(PANE, "b.ts");
  expect(paneState(PANE).openPaths).toEqual(["a.ts", "c.ts"]);
  expect(paneState(PANE).activePath).toBe("a.ts");

  store().closeFile(PANE, "a.ts");
  expect(paneState(PANE).openPaths).toEqual(["c.ts"]);
  expect(paneState(PANE).activePath).toBe("c.ts");
});

test("splitting a pane makes a split node with two leaves", () => {
  store().splitRight(PANE);
  const { splitTree, panes, activePaneId } = store();
  expect(splitTree.type).toBe("split");
  const leaves = leafPaneIds(splitTree);
  expect(leaves).toHaveLength(2);
  expect(leaves[0]).toBe(PANE);
  expect(panes).toHaveLength(2);
  expect(activePaneId).toBe(leaves[1]);
});

test("closing the last tab of a split pane collapses the split back to one leaf", () => {
  openPinned(PANE, "a.ts");
  store().splitRight(PANE);
  const second = store().activePaneId;
  expect(second).not.toBe(PANE);

  store().closeFile(second, "a.ts");
  expect(store().panes.map((p) => p.id)).toEqual([PANE]);
  expect(store().splitTree).toEqual({ type: "leaf", paneId: PANE });
  expect(store().activePaneId).toBe(PANE);
});

test("a diff tab carries the HEAD version, and null for a new file", () => {
  store().openFile(PANE, "src/plain.ts", "plain.ts", "ts", "plain");
  expect(isDiffTab(fileState("src/plain.ts"))).toBe(false);

  resetEditor();
  store().openDiffFile(PANE, "src/b.ts", "b.ts", "ts", "new text", "old text");
  const changed = fileState("src/b.ts");
  expect(isDiffTab(changed)).toBe(true);
  expect(changed.headContent).toBe("old text");
  expect(changed.content).toBe("new text");
  expect(paneState(PANE).activePath).toBe("src/b.ts");

  resetEditor();
  store().openDiffFile(PANE, "src/new.ts", "new.ts", "ts", "fresh", null);
  const added = fileState("src/new.ts");
  expect(isDiffTab(added)).toBe(true);
  expect(added.headContent).toBeNull();
});

test("editing marks a tab dirty, and saving clears it", () => {
  openPinned(PANE, "a.ts");
  store().updateContent("a.ts", "changed");
  expect(isDirty(fileState("a.ts"))).toBe(true);

  store().markSaved("a.ts", "changed");
  expect(isDirty(fileState("a.ts"))).toBe(false);
});

test("opening a file that has unsaved edits keeps those edits, in every pane that shows it", () => {
  openPinned(PANE, "a.ts");
  store().updateContent("a.ts", "my unsaved edit");
  store().splitRight(PANE);
  const second = store().activePaneId;

  // quick open, search and the file tree open a path with empty content first, then fill it from disk
  store().openFile(second, "a.ts", "a.ts", "ts", "");
  store().openFile(second, "a.ts", "a.ts", "ts", "disk text");

  expect(fileState("a.ts").content).toBe("my unsaved edit");
  expect(isDirty(fileState("a.ts"))).toBe(true);
  expect(paneState(second).openPaths).toContain("a.ts");
});

test("the layout written to localStorage holds tab paths only, never file contents", () => {
  store().openFile(PANE, "src/secret.ts", "secret.ts", "ts", "TOP-SECRET-BODY");
  const raw = must(storage.get(CODE_EDITOR_LAYOUT_KEY), "saved layout");
  expect(raw).not.toContain("TOP-SECRET-BODY");
  expect(parseSavedLayout(raw)?.panes[0]?.openPaths).toEqual(["src/secret.ts"]);
});

test("a saved layout is restored, and a new pane never reuses a restored id", async () => {
  const saved = {
    panes: [
      { id: "pane-7", openPaths: ["a.ts"], activePath: "a.ts" },
      { id: "pane-8", openPaths: [], activePath: null },
    ],
    activePaneId: "pane-8",
    splitTree: {
      type: "split",
      direction: "horizontal",
      sizePercent: 50,
      first: { type: "leaf", paneId: "pane-7" },
      second: { type: "leaf", paneId: "pane-8" },
    },
  };
  storage.set(CODE_EDITOR_LAYOUT_KEY, JSON.stringify(saved));

  vi.resetModules();
  const fresh = await import("../src/components/code/editor-store");
  const restored = fresh.useEditorStore.getState();
  expect(restored.panes.map((p) => p.openPaths)).toEqual([["a.ts"], []]);
  expect(restored.activePaneId).toBe("pane-8");

  restored.splitRight("pane-7");
  const created = fresh.useEditorStore.getState().activePaneId;
  expect(created).not.toBe("pane-7");
  expect(created).not.toBe("pane-8");
});

test("a malformed or inconsistent saved layout is ignored", () => {
  expect(parseSavedLayout("{not json")).toBeNull();
  expect(
    parseSavedLayout(JSON.stringify({ panes: [], activePaneId: "x", splitTree: { type: "leaf", paneId: "y" } }))
  ).toBeNull();
  expect(
    parseSavedLayout(
      JSON.stringify({
        panes: [{ id: "pane-1", openPaths: [], activePath: null }],
        activePaneId: "missing",
        splitTree: { type: "leaf", paneId: "pane-1" },
      })
    )
  ).toBeNull();
});

test("the narrow empty editor exposes a touch-sized file picker", () => {
  const html = renderToStaticMarkup(createElement(EmptyEditorState, { showFilePicker: true, onOpenFiles: () => {} }));

  expect(html).toContain('type="button"');
  expect(html).toContain("Browse files");
  expect(html).toContain("min-h-10");
});

test("the empty editor does not add a file picker when its sidebar is visible", () => {
  const html = renderToStaticMarkup(createElement(EmptyEditorState, { showFilePicker: false, onOpenFiles: () => {} }));

  expect(html).not.toContain("Browse files");
});

test("opening a diff tab over a file with unsaved edits keeps the file dirty", () => {
  store().openFile(PANE, "/w/a.ts", "a.ts", ".ts", "disk");
  store().updateContent("/w/a.ts", "edited");

  store().openDiffFile(PANE, "/w/a.ts", "a.ts", ".ts", "edited", "head");

  const file = must(store().fileCache.get("/w/a.ts"), "file");
  expect(file.content).toBe("edited");
  expect(file.savedContent).toBe("disk");
  expect(isDirty(file)).toBe(true);
  expect(file.headContent).toBe("head");
});

test("save acknowledgement preserves edits made while the save was pending", () => {
  store().openFile(PANE, "a.ts", "a.ts", "ts", "old");
  store().updateContent("a.ts", "sent");
  store().updateContent("a.ts", "typed while saving");
  store().markSaved("a.ts", "sent");
  expect(fileState("a.ts").content).toBe("typed while saving");
  expect(fileState("a.ts").savedContent).toBe("sent");
  expect(isDirty(fileState("a.ts"))).toBe(true);
});
