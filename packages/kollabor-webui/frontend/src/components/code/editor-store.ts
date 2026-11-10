import { create } from "zustand";

// shared file data (content is synced across panes showing the same file).
// a diff tab is a file tab that carries `headContent`: the HEAD version of the
// file, or null when the file has no HEAD version (a new file).
export interface FileData {
  bufferId: number;
  path: string;
  name: string;
  ext: string;
  content: string;
  savedContent: string;
  loading: boolean;
  pinned: boolean;
  saveRevision?: number;
  saveNotice?: { owner: string; epoch: number; message: string; stale: boolean };
  virtual?: boolean; // immutable content without a corresponding disk path
  headContent?: string | null; // diff tabs only
}

let nextBufferId = 1;

// a single pane's view state (paths only, content from fileCache)
export interface EditorPane {
  id: string;
  openPaths: string[];
  activePath: string | null;
}

// split tree node - either a leaf (pane) or a branch (split)
export type SplitNode =
  | { type: "leaf"; paneId: string }
  | {
      type: "split";
      direction: "horizontal" | "vertical";
      first: SplitNode;
      second: SplitNode;
      sizePercent: number; // % of space given to first child
    };

export type SidebarView = "files" | "search" | "config" | "git";

export interface PendingReveal {
  path: string;
  line: number;
  column: number;
}

export interface EditorConfig {
  fontSize: number;
  tabSize: number;
  wordWrap: "off" | "on" | "wordWrapColumn";
  minimap: boolean;
  lineNumbers: "on" | "off" | "relative";
  renderWhitespace: "none" | "boundary" | "all";
}

// the only tab state written to localStorage: pane tab paths and the split layout.
// file contents never go there.
export const CODE_EDITOR_LAYOUT_KEY = "kollab:code-editor:v1";

export interface SavedLayout {
  panes: EditorPane[];
  activePaneId: string;
  splitTree: SplitNode;
}

const EDITOR_CONFIG_KEY = "kollab:code-editor-config:v1";
const DEFAULT_CONFIG: EditorConfig = {
  fontSize: 11,
  tabSize: 2,
  wordWrap: "on",
  minimap: false,
  lineNumbers: "on",
  renderWhitespace: "boundary",
};

// the browser's localStorage, or null where there is none (node tests, blocked storage)
function browserStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

function loadEditorConfig(): EditorConfig {
  if (typeof window === "undefined") return DEFAULT_CONFIG;
  try {
    const saved = localStorage.getItem(EDITOR_CONFIG_KEY);
    if (saved) return { ...DEFAULT_CONFIG, ...JSON.parse(saved) };
  } catch {}
  return DEFAULT_CONFIG;
}

interface EditorStore {
  // shared file cache: the only place file contents live (never persisted)
  fileCache: Map<string, FileData>;

  // panes
  panes: EditorPane[];
  activePaneId: string;
  splitTree: SplitNode;

  // sidebar view
  sidebarView: SidebarView;
  setSidebarView: (view: SidebarView) => void;
  searchPanelVisible: boolean;
  toggleSearchPanel: () => void;
  pendingReveal: PendingReveal | null;
  setPendingReveal: (reveal: PendingReveal | null) => void;

  // editor config
  editorConfig: EditorConfig;
  updateEditorConfig: (partial: Partial<EditorConfig>) => void;

  // pane-scoped actions
  openFile: (paneId: string, path: string, name: string, ext: string, content: string) => void;
  openVirtualFile: (paneId: string, path: string, name: string, ext: string, content: string) => void;
  closeFile: (paneId: string, path: string) => void;
  setActiveFile: (paneId: string, path: string) => void;
  updateContent: (path: string, content: string) => void;
  markSaved: (path: string, content: string) => void;
  setFileLoading: (path: string, loading: boolean) => void;
  pinFile: (path: string) => void;

  // diff tab: `modified` is the working copy, `headContent` the HEAD version (null = no HEAD version)
  openDiffFile: (
    paneId: string,
    path: string,
    name: string,
    ext: string,
    modified: string,
    headContent: string | null
  ) => void;

  // reorder tabs
  reorderFiles: (paneId: string, fromIndex: number, toIndex: number) => void;

  // pane management
  splitRight: (paneId: string) => void;
  splitDown: (paneId: string) => void;
  setActivePane: (paneId: string) => void;
  closePane: (paneId: string) => void;
}

// helper to replace a pane with a split in the tree
export function replacePaneInTree(
  node: SplitNode,
  paneId: string,
  replacement: SplitNode
): SplitNode {
  if (node.type === "leaf") {
    return node.paneId === paneId ? replacement : node;
  }
  return {
    ...node,
    first: replacePaneInTree(node.first, paneId, replacement),
    second: replacePaneInTree(node.second, paneId, replacement),
  };
}

// helper to remove a pane from the tree; a split with one side gone collapses to the other side
export function removePaneFromTree(node: SplitNode, paneId: string): SplitNode | null {
  if (node.type === "leaf") {
    return node.paneId === paneId ? null : node;
  }
  const newFirst = removePaneFromTree(node.first, paneId);
  const newSecond = removePaneFromTree(node.second, paneId);

  if (!newFirst && !newSecond) return null;
  if (!newFirst) return newSecond;
  if (!newSecond) return newFirst;
  return { ...node, first: newFirst, second: newSecond };
}

// the pane ids at the leaves of a tree, left to right
export function leafPaneIds(node: SplitNode): string[] {
  if (node.type === "leaf") return [node.paneId];
  return [...leafPaneIds(node.first), ...leafPaneIds(node.second)];
}

// shows a path in a pane: activates it if the pane already has it, otherwise
// replaces the pane's preview tab (an unpinned file) or appends a new tab
function showFileTab(
  panes: EditorPane[],
  paneId: string,
  path: string,
  cache: Map<string, FileData>
): EditorPane[] {
  return panes.map((p) => {
    if (p.id !== paneId) return p;
    if (p.openPaths.includes(path)) return { ...p, activePath: path };
    const previewIdx = p.openPaths.findIndex((fp) => {
      const fd = cache.get(fp);
      return fd !== undefined && !fd.pinned;
    });
    const newOpenPaths = [...p.openPaths];
    if (previewIdx >= 0) {
      newOpenPaths[previewIdx] = path;
    } else {
      newOpenPaths.push(path);
    }
    return { ...p, openPaths: newOpenPaths, activePath: path };
  });
}

function parseJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return undefined;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSplitNode(value: unknown): value is SplitNode {
  if (!isRecord(value)) return false;
  if (value.type === "leaf") return typeof value.paneId === "string";
  return (
    value.type === "split" &&
    (value.direction === "horizontal" || value.direction === "vertical") &&
    typeof value.sizePercent === "number" &&
    isSplitNode(value.first) &&
    isSplitNode(value.second)
  );
}

// reads a saved layout. anything malformed, or whose pane ids do not match the split tree, gives null.
export function parseSavedLayout(raw: string | null): SavedLayout | null {
  if (!raw) return null;
  const data = parseJson(raw);
  if (!isRecord(data) || !Array.isArray(data.panes)) return null;
  const activePaneId = data.activePaneId;
  const splitTree = data.splitTree;
  if (typeof activePaneId !== "string" || !isSplitNode(splitTree)) return null;

  const items: unknown[] = data.panes;
  const panes: EditorPane[] = [];
  for (const item of items) {
    if (!isRecord(item) || typeof item.id !== "string" || !Array.isArray(item.openPaths)) return null;
    const paths: unknown[] = item.openPaths;
    panes.push({
      id: item.id,
      openPaths: paths.filter((p): p is string => typeof p === "string"),
      activePath: typeof item.activePath === "string" ? item.activePath : null,
    });
  }

  const ids = panes.map((p) => p.id);
  const leaves = leafPaneIds(splitTree);
  const consistent =
    ids.length === leaves.length &&
    ids.every((id) => leaves.includes(id)) &&
    ids.includes(activePaneId);
  return consistent ? { panes, activePaneId, splitTree } : null;
}

function saveLayout(state: Pick<EditorStore, "panes" | "activePaneId" | "splitTree">): void {
  const storage = browserStorage();
  if (!storage) return;
  const layout: SavedLayout = {
    panes: state.panes.map((p) => {
      const openPaths = p.openPaths.filter((path) => !path.includes("\0"));
      return { id: p.id, openPaths, activePath: p.activePath && openPaths.includes(p.activePath) ? p.activePath : null };
    }),
    activePaneId: state.activePaneId,
    splitTree: state.splitTree,
  };
  try {
    storage.setItem(CODE_EDITOR_LAYOUT_KEY, JSON.stringify(layout));
  } catch {}
}

function readSavedLayout(): SavedLayout | null {
  try {
    return parseSavedLayout(browserStorage()?.getItem(CODE_EDITOR_LAYOUT_KEY) ?? null);
  } catch {
    return null;
  }
}

let paneIdCounter = 0;
function generatePaneId(): string {
  return `pane-${++paneIdCounter}`;
}

// restored pane ids keep their numbers, so a new pane never reuses one
function advancePaneIdCounter(ids: string[]): void {
  for (const id of ids) {
    const match = /^pane-(\d+)$/.exec(id);
    if (match) paneIdCounter = Math.max(paneIdCounter, Number(match[1]));
  }
}

function freshLayout(): SavedLayout {
  const pane: EditorPane = { id: generatePaneId(), openPaths: [], activePath: null };
  return { panes: [pane], activePaneId: pane.id, splitTree: { type: "leaf", paneId: pane.id } };
}

export const useEditorStore = create<EditorStore>((set, get) => {
  const restored = readSavedLayout();
  if (restored) advancePaneIdCounter(restored.panes.map((p) => p.id));
  const layout = restored ?? freshLayout();

  // a new pane starts with the same tabs as the pane it splits from
  const splitPane = (paneId: string, direction: "horizontal" | "vertical") => {
    const store = get();
    const currentPane = store.panes.find((p) => p.id === paneId);
    if (!currentPane) return;

    const newPane: EditorPane = {
      id: generatePaneId(),
      openPaths: [...currentPane.openPaths],
      activePath: currentPane.activePath,
    };

    const replacement: SplitNode = {
      type: "split",
      direction,
      first: { type: "leaf", paneId },
      second: { type: "leaf", paneId: newPane.id },
      sizePercent: 50,
    };

    set((s) => ({
      panes: [...s.panes, newPane],
      splitTree: replacePaneInTree(s.splitTree, paneId, replacement),
      activePaneId: newPane.id,
    }));
  };

  return {
    fileCache: new Map(),
    panes: layout.panes,
    activePaneId: layout.activePaneId,
    splitTree: layout.splitTree,
    sidebarView: "files",
    setSidebarView: (view) => set({ sidebarView: view }),
    searchPanelVisible: false,
    pendingReveal: null,
    toggleSearchPanel: () => set((s) => {
      const isSearch = s.sidebarView === "search";
      return {
        searchPanelVisible: !isSearch,
        sidebarView: isSearch ? "files" : "search",
      };
    }),
    setPendingReveal: (reveal) => set({ pendingReveal: reveal }),
    editorConfig: loadEditorConfig(),
    updateEditorConfig: (partial) => set((s) => {
      const next = { ...s.editorConfig, ...partial };
      try { localStorage.setItem(EDITOR_CONFIG_KEY, JSON.stringify(next)); } catch {}
      return { editorConfig: next };
    }),

    openFile: (paneId, path, name, ext, content) => {
      const store = get();
      const pane = store.panes.find((p) => p.id === paneId);
      if (!pane) return;

      // add or update file cache
      const existing = store.fileCache.get(path);
      // A buffer with unsaved edits is the user's work: opening the path again (another pane, quick open,
      // search, the tree) only shows its tab. It never replaces the buffer with what the caller read.
      const fileData: FileData = existing
        ? isDirty(existing)
          ? { ...existing, loading: false }
          : { ...existing, content, savedContent: content, loading: false }
        : { bufferId: nextBufferId++, path, name, ext, content, savedContent: content, loading: false, pinned: false };

      const newCache = new Map(store.fileCache);
      newCache.set(path, fileData);

      set({ fileCache: newCache, panes: showFileTab(store.panes, paneId, path, store.fileCache) });
    },

    openVirtualFile: (paneId, path, name, ext, content) => {
      if (!path.includes("\0")) return;
      get().openFile(paneId, path, name, ext, content);
      set((s) => {
        const existing = s.fileCache.get(path);
        if (!existing) return s;
        const fileCache = new Map(s.fileCache);
        fileCache.set(path, { ...existing, virtual: true });
        return { fileCache };
      });
    },

    openDiffFile: (paneId, path, name, ext, modified, headContent) => {
      const store = get();
      const pane = store.panes.find((p) => p.id === paneId);
      if (!pane) return;

      // A buffer with unsaved edits stays as it is: the diff only adds the HEAD side. Resetting savedContent to
      // the buffer would mark it clean, and closing the tab would then drop the edits with no prompt.
      const existing = store.fileCache.get(path);
      const fileData: FileData =
        existing && isDirty(existing)
          ? { ...existing, loading: false, headContent }
          : { bufferId: nextBufferId++, path, name, ext, content: modified, savedContent: modified, loading: false, pinned: false, headContent };

      const newCache = new Map(store.fileCache);
      newCache.set(path, fileData);

      set({ fileCache: newCache, panes: showFileTab(store.panes, paneId, path, store.fileCache) });
    },

    closeFile: (paneId, path) => {
      const store = get();
      const pane = store.panes.find((p) => p.id === paneId);
      if (!pane) return;

      const newOpenPaths = pane.openPaths.filter((p) => p !== path);
      let newActivePath = pane.activePath;
      if (pane.activePath === path) {
        // activate the neighbour on the left, or the new first tab when there is none
        const idx = pane.openPaths.indexOf(path);
        newActivePath = newOpenPaths[Math.max(0, idx - 1)] ?? null;
      }

      const newPanes = store.panes.map((p) =>
        p.id === paneId ? { ...p, openPaths: newOpenPaths, activePath: newActivePath } : p
      );

      // evict from cache if no pane holds this file anymore
      const stillOpen = newPanes.some((p) => p.openPaths.includes(path));
      const newCache = stillOpen ? store.fileCache : (() => {
        const c = new Map(store.fileCache);
        c.delete(path);
        return c;
      })();

      // a pane with no files left closes too, unless it is the only pane
      if (newOpenPaths.length === 0 && store.panes.length > 1) {
        set({ fileCache: newCache });
        get().closePane(paneId);
      } else {
        set({ panes: newPanes, fileCache: newCache });
      }
    },

    setActiveFile: (paneId, path) => {
      set((s) => ({
        panes: s.panes.map((p) => (p.id === paneId ? { ...p, activePath: path } : p)),
      }));
    },

    updateContent: (path, content) => {
      // editing auto-pins the file
      set((s) => {
        const newCache = new Map(s.fileCache);
        const existing = newCache.get(path);
        if (existing && !existing.virtual) {
          newCache.set(path, { ...existing, content, pinned: true });
        }
        return { fileCache: newCache };
      });
    },

    markSaved: (path, content) => {
      set((s) => {
        const newCache = new Map(s.fileCache);
        const existing = newCache.get(path);
        if (existing) {
          newCache.set(path, { ...existing, savedContent: content, saveNotice: undefined, saveRevision: (existing.saveRevision ?? 0) + 1 });
        }
        return { fileCache: newCache };
      });
    },

    setFileLoading: (path, loading) => {
      set((s) => {
        const newCache = new Map(s.fileCache);
        const existing = newCache.get(path);
        if (existing) {
          newCache.set(path, { ...existing, loading });
        }
        return { fileCache: newCache };
      });
    },

    pinFile: (path) => {
      set((s) => {
        const newCache = new Map(s.fileCache);
        const existing = newCache.get(path);
        if (existing) {
          newCache.set(path, { ...existing, pinned: true });
        }
        return { fileCache: newCache };
      });
    },

    reorderFiles: (paneId, fromIndex, toIndex) => {
      set((s) => ({
        panes: s.panes.map((p) => {
          if (p.id !== paneId) return p;
          const newPaths = [...p.openPaths];
          const [moved] = newPaths.splice(fromIndex, 1);
          newPaths.splice(toIndex, 0, moved);
          return { ...p, openPaths: newPaths };
        }),
      }));
    },

    splitRight: (paneId) => splitPane(paneId, "horizontal"),
    splitDown: (paneId) => splitPane(paneId, "vertical"),

    setActivePane: (paneId) => {
      set({ activePaneId: paneId });
    },

    closePane: (paneId) => {
      const store = get();
      if (store.panes.length === 1) return; // never close last pane

      const newTree = removePaneFromTree(store.splitTree, paneId);
      if (!newTree) return;

      const newPanes = store.panes.filter((p) => p.id !== paneId);
      const newActiveId =
        store.activePaneId === paneId ? newPanes[0].id : store.activePaneId;

      set({
        panes: newPanes,
        splitTree: newTree,
        activePaneId: newActiveId,
      });
    },
  };
});

// write the tab layout whenever panes or the split tree change; only paths and ids are written
useEditorStore.subscribe((state, prev) => {
  if (
    state.panes !== prev.panes ||
    state.splitTree !== prev.splitTree ||
    state.activePaneId !== prev.activePaneId
  ) {
    saveLayout(state);
  }
});

// Retain the outcome only on the buffer that initiated the operation.
export function setBufferSaveNotice(path: string, bufferId: number, notice: FileData["saveNotice"]) {
  useEditorStore.setState((state) => {
    const current = state.fileCache.get(path);
    if (!current || current.bufferId !== bufferId) return state;
    const fileCache = new Map(state.fileCache);
    fileCache.set(path, { ...current, saveNotice: notice });
    return { fileCache };
  });
}

// selectors
export function isDirty(file: FileData): boolean {
  return file.content !== file.savedContent;
}

// a diff tab carries the HEAD version (null for a new file)
export function isDiffTab(file: FileData): boolean {
  return file.headContent !== undefined;
}

// get pane data helper
export function usePane(paneId: string) {
  const panes = useEditorStore((s) => s.panes);
  const fileCache = useEditorStore((s) => s.fileCache);
  const pane = panes.find((p) => p.id === paneId);

  if (!pane) return { pane: null, openFiles: [], activeFile: null };

  const openFiles = pane.openPaths
    .map((path) => fileCache.get(path))
    .filter((f): f is FileData => f !== undefined);

  const activeFile = pane.activePath ? fileCache.get(pane.activePath) ?? null : null;

  return { pane, openFiles, activeFile };
}
