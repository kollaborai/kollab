import { codeFetch, unwrapCodeData } from "@/lib/code-fetch";
import { isDirty, useEditorStore } from "@/components/code/editor-store";
import { rememberMtime, rememberVersion } from "@/components/code/file-save";
import { editorOwner, editorOwnerEpoch, releaseClosedBufferOwner } from "@/components/code/workspace-tabs";

export function fileNameOf(absPath: string): string {
  return absPath.slice(absPath.lastIndexOf("/") + 1);
}

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

/** The file's text, or null when the engine refuses or cannot read it (too large, binary, gone). */
export type FileRead = { content: string; mtime?: number; version?: string };
export function rememberFileRead(path: string, data: FileRead): void {
  rememberMtime(path, data.mtime);
  rememberVersion(path, data.version);
}
export async function readFileContent(workspace: string, absPath: string, expectedBufferId?: number): Promise<FileRead | null> {
  const owner = editorOwner();
  const epoch = editorOwnerEpoch();
  try {
    const res = await codeFetch(`/api/fs/file?workspace=${encodeURIComponent(workspace)}&path=${encodeURIComponent(absPath)}`);
    if (!res.ok) return null;
    const raw: unknown = await res.json();
    if (owner !== editorOwner() || epoch !== editorOwnerEpoch() || (expectedBufferId !== undefined &&
      useEditorStore.getState().fileCache.get(absPath)?.bufferId !== expectedBufferId)) return null;
    const data = unwrapCodeData<{ content?: string; mtime?: number; version?: string }>(raw);
    return { content: data.content ?? "", mtime: data.mtime, version: data.version };
  } catch {
    return null;
  }
}

const pendingOpens = new Map<string, Promise<boolean>>();

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
  releaseClosedBufferOwner(absPath);
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
  const owner = editorOwner();
  const epoch = editorOwnerEpoch();
  const existingId = cached?.bufferId;
  const key = `${epoch}\0${workspace}\0${absPath}\0${existingId ?? "new"}`;
  const pending = cached?.loading ? pendingOpens.get(key) : undefined;
  if (pending) {
    store.openFile(paneId, absPath, name, ext, cached!.content);
    store.setFileLoading(absPath, true);
    if (options.pin) store.pinFile(absPath);
    const ok = await pending;
    if (!ok && owner === editorOwner() && epoch === editorOwnerEpoch() &&
        useEditorStore.getState().fileCache.get(absPath)?.bufferId === existingId) {
      useEditorStore.getState().closeFile(paneId, absPath);
    }
    return ok;
  }
  store.openFile(paneId, absPath, name, ext, "");
  if (options.pin) store.pinFile(absPath);
  store.setFileLoading(absPath, true);
  const bufferId = useEditorStore.getState().fileCache.get(absPath)?.bufferId;
  const pendingKey = `${epoch}\0${workspace}\0${absPath}\0${bufferId}`;
  const loading = (async () => {
    const read = await readFileContent(workspace, absPath, bufferId);
    const current = useEditorStore.getState().fileCache.get(absPath);
    if (!current || owner !== editorOwner() || epoch !== editorOwnerEpoch() || bufferId !== current.bufferId) return false;
    if (read === null) {
      useEditorStore.getState().closeFile(paneId, absPath);
      return false;
    }
    if (!isDirty(current)) {
      rememberFileRead(absPath, read);
      useEditorStore.getState().openFile(paneId, absPath, name, ext, read.content);
    } else {
      useEditorStore.getState().setFileLoading(absPath, false);
    }
    return true;
  })();
  pendingOpens.set(pendingKey, loading);
  try { return await loading; } finally { pendingOpens.delete(pendingKey); }
}

/** Opens a diff tab: working copy against the HEAD content the git panel passes (null = new file). */
export async function openWorkspaceDiff(workspace: string, absPath: string, headContent: string | null): Promise<void> {
  const name = fileNameOf(absPath);
  const owner = editorOwner();
  const epoch = editorOwnerEpoch();
  const cached = useEditorStore.getState().fileCache.get(absPath);
  const read = cached ? null : await readFileContent(workspace, absPath);
  if (owner !== editorOwner() || epoch !== editorOwnerEpoch()) return;
  const store = useEditorStore.getState();
  const current = store.fileCache.get(absPath);
  if (!current && !read) return;
  if (!current && read) rememberFileRead(absPath, read);
  store.openDiffFile(store.activePaneId, absPath, name, extensionOf(name), current?.content ?? read!.content, headContent);
}
