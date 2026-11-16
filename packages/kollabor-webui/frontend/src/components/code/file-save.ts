import { bufferBelongsHere, editorOwner, editorOwnerEpoch } from "@/components/code/workspace-tabs";
import { useEditorStore } from "@/components/code/editor-store";
import { codeFetch } from "@/lib/code-fetch";

/**
 * Remembers the on-disk mtime (ms) each file had when it was read or last saved, so a save can ask the daemon
 * to refuse when the file changed underneath (409 `stale_write`). Kept outside the editor store: it is not
 * part of what the user sees or edits.
 */
const mtimes = new Map<string, number>();
const versions = new Map<string, string>();
// One browser must not send overlapping writes to the same path.
const pendingSaves = new Set<string>();
const keyFor = (path: string, owner = editorOwner()) => `${owner}\0${path}`;
export function rememberVersion(path: string, version: unknown): void {
  if (typeof version === "string" && version) versions.set(keyFor(path), version);
}

export function rememberMtime(path: string, mtime: unknown): void {
  if (typeof mtime === "number" && Number.isFinite(mtime)) mtimes.set(keyFor(path), mtime);
}

export function mtimeOf(path: string): number | undefined {
  return mtimes.get(keyFor(path));
}

export function forgetMtime(path: string): void {
  mtimes.delete(keyFor(path));
  versions.delete(keyFor(path));
}

export type SaveResult = { ok: true } | { ok: false; stale: boolean; message: string };

function errorOf(payload: unknown): { code: string; message: string } {
  const error = payload && typeof payload === "object" && "error" in payload ? payload.error : null;
  const record = error && typeof error === "object" ? (error as Record<string, unknown>) : {};
  return {
    code: typeof record.code === "string" ? record.code : "",
    message: typeof record.message === "string" && record.message ? record.message : "save failed",
  };
}

/** Writes a file through the session's daemon. `force` overwrites a file that changed on disk. */
export async function saveWorkspaceFile(
  workspace: string,
  path: string,
  content: string,
  options: { force?: boolean } = {},
): Promise<SaveResult> {
  if (path.includes("\0") || useEditorStore.getState().fileCache.get(path)?.virtual) {
    return { ok: false, stale: false, message: "This view is read-only." };
  }
  if (!bufferBelongsHere(path)) return { ok: false, stale: false, message: "Switch back to the original device and folder to save this file." };
  if (pendingSaves.has(path)) return { ok: false, stale: false, message: "Another save for this file is in progress." };
  pendingSaves.add(path);
  const owner = editorOwner();
  const epoch = editorOwnerEpoch();
  const bufferId = useEditorStore.getState().fileCache.get(path)?.bufferId;
  const body: Record<string, unknown> = { workspace, content };
  const version = versions.get(keyFor(path));
  if (version !== undefined) body.expectedVersion = version;
  const expected = mtimes.get(keyFor(path));
  if (expected !== undefined && version === undefined) body.expectedMtime = expected;
  if (options.force) body.force = true;
  try {
    const res = await codeFetch(`/api/fs/file?path=${encodeURIComponent(path)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const raw: unknown = await res.json().catch(() => null);
    if (owner !== editorOwner() || epoch !== editorOwnerEpoch() ||
        bufferId !== useEditorStore.getState().fileCache.get(path)?.bufferId) {
      const failure = res.ok ? null : errorOf(raw);
      return { ok: false, stale: false, message: failure && res.status < 500 && res.status !== 408 &&
        failure.code !== "save_outcome_unknown" && failure.code !== "request_failed"
        ? failure.message : "The save may have completed while the workspace changed; check the file before retrying." };
    }
    if (res.ok) {
      const data = raw && typeof raw === "object" && "data" in raw ? raw.data : raw;
      if (data && typeof data === "object" && "mtime" in data) rememberMtime(path, data.mtime);
      if (data && typeof data === "object" && "version" in data) rememberVersion(path, data.version);
      return { ok: true };
    }
    const { code, message } = errorOf(raw);
    return { ok: false, stale: code === "stale_write", message: res.status >= 500
      ? `${message} The save may have completed; check the file before retrying.` : message };
  } catch {
    return { ok: false, stale: false, message: "The save result is unknown; check the file before retrying." };
  } finally {
    pendingSaves.delete(path);
  }
}
