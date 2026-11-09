import { codeFetch } from "@/lib/code-fetch";

/**
 * Remembers the on-disk mtime (ms) each file had when it was read or last saved, so a save can ask the daemon
 * to refuse when the file changed underneath (409 `stale_write`). Kept outside the editor store: it is not
 * part of what the user sees or edits.
 */
const mtimes = new Map<string, number>();

export function rememberMtime(path: string, mtime: unknown): void {
  if (typeof mtime === "number" && Number.isFinite(mtime)) mtimes.set(path, mtime);
}

export function mtimeOf(path: string): number | undefined {
  return mtimes.get(path);
}

export function forgetMtime(path: string): void {
  mtimes.delete(path);
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
  const body: Record<string, unknown> = { workspace, content };
  const expected = mtimes.get(path);
  if (expected !== undefined) body.expectedMtime = expected;
  if (options.force) body.force = true;
  try {
    const res = await codeFetch(`/api/fs/file?path=${encodeURIComponent(path)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const raw: unknown = await res.json().catch(() => null);
    if (res.ok) {
      const data = raw && typeof raw === "object" && "data" in raw ? raw.data : raw;
      if (data && typeof data === "object" && "mtime" in data) rememberMtime(path, data.mtime);
      return { ok: true };
    }
    const { code, message } = errorOf(raw);
    return { ok: false, stale: code === "stale_write", message };
  } catch {
    return { ok: false, stale: false, message: "save failed" };
  }
}
