import { useCallback, useEffect, useRef, useState } from "react";
import { codeFetch, unwrapCodeData } from "@/lib/code-fetch";
import { openWorkspaceFile } from "@/components/code/open-file";

type Entry = { name: string; path: string; type: "file" | "dir" };
type Page = { path: string; parent: string; entries: Entry[]; next: string | null };

export function gitStatusPath(workspace: string, path: string): string {
  return path.slice(workspace.length).replace(/^[\\/]/, "").replace(/\\/g, "/");
}

/** Bounded folder-by-folder navigation when a full tree cannot fit in one response. */
export function LazyFileList({ workspace, onFileSelect, revision, gitStatus, filter, onCreateFile, onCreateFolder, onRename, onDelete }: {
  workspace: string; onFileSelect?: () => void; revision: number; gitStatus: Record<string, string>; filter: string;
  onCreateFile: (path: string) => void; onCreateFolder: (path: string) => void;
  onRename: (path: string, name: string) => void; onDelete: (path: string, name: string) => void;
}) {
  const [path, setPath] = useState(workspace);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [parent, setParent] = useState(workspace);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);

  const load = useCallback(async (folder: string, cursor: string | null, current: number) => {
    setLoading(true);
    setError("");
    try {
      const suffix = cursor ? `&after=${encodeURIComponent(cursor)}` : "";
      const response = await codeFetch(`/api/fs/list?path=${encodeURIComponent(folder)}${suffix}`);
      const page = unwrapCodeData<Page>(await response.json());
      if (!response.ok) throw new Error("Could not load this folder.");
      if (generation.current !== current) return;
      setEntries((old) => cursor ? [...old, ...page.entries] : page.entries);
      setNext(page.next);
      setParent(page.parent);
    } catch (e) {
      if (generation.current === current) setError(e instanceof Error ? e.message : "Could not load this folder.");
    } finally {
      if (generation.current === current) setLoading(false);
    }
  }, []);

  useEffect(() => { setPath(workspace); }, [workspace]);
  useEffect(() => {
    const current = ++generation.current;
    setEntries([]);
    setNext(null);
    void load(path, null, current);
    return () => { generation.current += 1; };
  }, [path, load, revision]);

  return (
    <div className="px-2 py-1 text-xs">
      <p className="pb-1 text-muted-foreground">Large folder: browse one folder at a time. The filter applies to loaded entries.</p>
      <div className="break-all text-muted-foreground">{path}</div>
      {path !== workspace && <button type="button" className="block w-full py-1 text-left" onClick={() => setPath(parent)}>..</button>}
      <div className="flex gap-2 py-1">
        <button type="button" onClick={() => onCreateFile(path)}>New file here</button>
        <button type="button" onClick={() => onCreateFolder(path)}>New folder here</button>
      </div>
      {entries.filter((entry) => entry.name.toLowerCase().includes(filter.toLowerCase())).map((entry) => (
        <div key={entry.path} className="flex items-center gap-2">
        <button type="button" className="min-w-0 flex-1 truncate py-1 text-left hover:bg-accent"
          onClick={() => {
            if (entry.type === "dir") setPath(entry.path);
            else { onFileSelect?.(); void openWorkspaceFile(workspace, entry.path); }
          }}>
          {entry.name}{entry.type === "dir" ? "/" : ""}
        </button>
        {gitStatus[gitStatusPath(workspace, entry.path)] &&
          <span>{gitStatus[gitStatusPath(workspace, entry.path)]}</span>}
        <button type="button" aria-label={`Rename ${entry.name}`} onClick={() => onRename(entry.path, entry.name)}>Rename</button>
        <button type="button" aria-label={`Delete ${entry.name}`} onClick={() => onDelete(entry.path, entry.name)}>Delete</button>
        </div>
      ))}
      {next && <button type="button" disabled={loading} className="block py-1" onClick={() => void load(path, next, generation.current)}>Load more files</button>}
      {loading && <p>Loading…</p>}
      {error && <p role="alert" className="text-destructive">{error}</p>}
      {!loading && !error && entries.length === 0 && <p>No files</p>}
    </div>
  );
}
