import { type FileData, isDirty, useEditorStore } from "@/components/code/editor-store";

// the folder the saved editor tabs were opened in; a reload into another folder starts with no tabs
const TABS_WORKSPACE_KEY = "kollab:code-editor-tabs-workspace:v1";

export function savedTabsWorkspace(): string | null {
  try {
    return window.localStorage.getItem(TABS_WORKSPACE_KEY);
  } catch {
    return null;
  }
}

export function rememberTabsWorkspace(workspace: string): void {
  try {
    window.localStorage.setItem(TABS_WORKSPACE_KEY, workspace);
  } catch {
    // storage blocked or full: the tabs are not tied to a folder across a reload
  }
}

/**
 * Closes every tab and split, keeping the first pane. Tabs with unsaved edits stay open (the editor store has no
 * reset action): dropping them silently would lose the user's work.
 */
export function resetEditorTabs(): void {
  const { panes, fileCache } = useEditorStore.getState();
  const first = panes[0];
  if (!first) return;
  const kept = new Map<string, FileData>();
  for (const [path, file] of fileCache) if (isDirty(file)) kept.set(path, file);
  const openPaths = [...kept.keys()];
  useEditorStore.setState({
    panes: [{ id: first.id, openPaths, activePath: openPaths[0] ?? null }],
    activePaneId: first.id,
    splitTree: { type: "leaf", paneId: first.id },
    fileCache: kept,
  });
}

/**
 * Ties the editor's tabs to `workspace`: tabs opened in another folder are dropped (those with unsaved edits stay).
 * Called before anything opens a file for this folder, so a view that opens one first never loses it to the reset.
 */
export function claimEditorWorkspace(workspace: string): void {
  const owner = savedTabsWorkspace();
  if (owner !== null && owner !== workspace) resetEditorTabs();
  rememberTabsWorkspace(workspace);
}
