import { codeFetch, unwrapCodeData } from "@/lib/code-fetch";
import { useEditorStore } from "@/components/code/editor-store";
import { rememberMtime } from "@/components/code/file-save";

export function fileNameOf(absPath: string): string {
  return absPath.slice(absPath.lastIndexOf("/") + 1);
}

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

/** The file's text, or null when the engine refuses or cannot read it (too large, binary, gone). */
export async function readFileContent(workspace: string, absPath: string): Promise<string | null> {
  try {
    const res = await codeFetch(`/api/fs/file?workspace=${encodeURIComponent(workspace)}&path=${encodeURIComponent(absPath)}`);
    if (!res.ok) return null;
    const raw: unknown = await res.json();
    const data = unwrapCodeData<{ content?: string; mtime?: number }>(raw);
    rememberMtime(absPath, data.mtime);
    return data.content ?? "";
  } catch {
    return null;
  }
}

/**
 * Opens a file in a pane (the active one unless `paneId` is given). Returns whether the file is open.
 *
 * The tab shows at once and fills in when the read lands. A failed read closes the tab again: an empty tab
 * saved over the file would erase it. A path the pane already has is only activated, and a path another pane
 * holds is shown from that buffer, so unsaved edits are never replaced by a read from disk.
 */
export async function openWorkspaceFile(
  workspace: string,
  absPath: string,
  options: { paneId?: string; pin?: boolean } = {},
): Promise<boolean> {
  const store = useEditorStore.getState();
  const paneId = options.paneId ?? store.activePaneId;
  const pane = store.panes.find((p) => p.id === paneId);
  if (!pane) return false;
  if (pane.openPaths.includes(absPath)) {
    store.setActiveFile(paneId, absPath);
    return true;
  }
  const name = fileNameOf(absPath);
  const ext = extensionOf(name);
  const cached = store.fileCache.get(absPath);
  if (cached && !cached.loading) {
    // already loaded, maybe with unsaved edits: show that buffer, never reload over it
    store.openFile(paneId, absPath, name, ext, cached.content);
    if (options.pin) store.pinFile(absPath);
    return true;
  }
  store.openFile(paneId, absPath, name, ext, "");
  if (options.pin) store.pinFile(absPath);
  store.setFileLoading(absPath, true);
  const content = await readFileContent(workspace, absPath);
  if (content === null) {
    useEditorStore.getState().closeFile(paneId, absPath);
    return false;
  }
  useEditorStore.getState().openFile(paneId, absPath, name, ext, content);
  return true;
}

/** Opens a diff tab: working copy against the HEAD content the git panel passes (null = new file). */
export async function openWorkspaceDiff(workspace: string, absPath: string, headContent: string | null): Promise<void> {
  const name = fileNameOf(absPath);
  const cached = useEditorStore.getState().fileCache.get(absPath);
  const modified = cached ? cached.content : await readFileContent(workspace, absPath);
  if (modified === null) return;
  const store = useEditorStore.getState();
  store.openDiffFile(store.activePaneId, absPath, name, extensionOf(name), modified, headContent);
}
