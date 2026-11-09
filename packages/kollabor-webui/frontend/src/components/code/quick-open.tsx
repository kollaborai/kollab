import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { File as FileIcon } from "lucide-react";
import { codeFetch, unwrapCodeData } from "@/lib/code-fetch";
import { cn } from "@/lib/utils";
import { useEditorStore } from "@/components/code/editor-store";
import { openWorkspaceFile } from "@/components/code/open-file";
import { fuzzyMatch, scanQuickFiles, type DirectoryPage } from "@/components/code/paged-quick-open";

interface TreeNode {
  name: string;
  path: string;
  type: string;
  ext?: string;
  children?: TreeNode[];
}

interface FlatFile {
  name: string;
  path: string;
  ext: string;
  relativePath: string;
}

function flattenTree(nodes: TreeNode[], rootPath: string): FlatFile[] {
  const result: FlatFile[] = [];
  const walk = (items: TreeNode[]) => {
    for (const node of items) {
      if (node.type === "file") {
        const rel = node.path.startsWith(rootPath)
          ? node.path.slice(rootPath.length + 1)
          : node.path;
        result.push({
          name: node.name,
          path: node.path,
          ext: node.ext ?? "",
          relativePath: rel,
        });
      }
      if (node.type === "dir" && Array.isArray(node.children)) {
        walk(node.children);
      }
    }
  };
  walk(nodes);
  return result;
}

interface QuickOpenProps {
  open: boolean;
  onClose: () => void;
  workspace: string;
}

export function QuickOpen({ open, onClose, workspace }: QuickOpenProps) {
  const [query, setQuery] = useState("");
  const [files, setFiles] = useState<FlatFile[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [error, setError] = useState("");
  const [largeTree, setLargeTree] = useState(false);
  const [searching, setSearching] = useState(false);
  const [partial, setPartial] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const activePaneId = useEditorStore((s) => s.activePaneId);

  useEffect(() => {
    if (!open || !workspace) return;
    setQuery("");
    setFiles([]);
    setError("");
    setLargeTree(false);
    setPartial(false);
    setSelectedIndex(0);
    let cancelled = false;
    codeFetch(`/api/fs/tree?workspace=${encodeURIComponent(workspace)}`)
      .then((r) => r.json())
      .then((raw: unknown) => {
        if (typeof raw === "object" && raw !== null && "error" in raw &&
            typeof raw.error === "object" && raw.error !== null && "code" in raw.error && raw.error.code === "tree_too_large") {
          if (!cancelled) setLargeTree(true);
          return;
        }
        const data = unwrapCodeData<{ tree?: TreeNode[] }>(raw);
        if (!cancelled && data.tree) setFiles(flattenTree(data.tree, workspace));
      })
      .catch((e: unknown) => { if (!cancelled) setError(e instanceof Error ? e.message : "Could not load files."); });
    const timer = setTimeout(() => inputRef.current?.focus(), 50);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [open, workspace]);

  useEffect(() => {
    if (!open || !largeTree) return;
    let cancelled = false;
    setFiles([]);
    setError("");
    setPartial(false);
    setSearching(true);
    const timer = setTimeout(() => {
      void scanQuickFiles(workspace, query, async (path, after) => {
        const suffix = after ? `&after=${encodeURIComponent(after)}` : "";
        const response = await codeFetch(`/api/fs/list?path=${encodeURIComponent(path)}${suffix}`);
        const data = unwrapCodeData<DirectoryPage>(await response.json());
        if (!response.ok) throw new Error("Could not search this folder.");
        return data;
      }, () => cancelled).then((result) => {
        if (!cancelled) { setFiles(result.files); setPartial(result.partial); }
      }).catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not search files.");
      }).finally(() => { if (!cancelled) setSearching(false); });
    }, 150);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [open, largeTree, workspace, query]);

  const filtered = query
    ? files.filter((f) => fuzzyMatch(query, f.relativePath))
    : files;

  const handleSelect = useCallback(
    async (file: FlatFile) => {
      if (!activePaneId) return;
      onClose();
      await openWorkspaceFile(workspace, file.path, { pin: true });
    },
    [onClose, activePaneId, workspace]
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        // Escape closes this picker only, not the floating panel behind it
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(0, Math.min(i + 1, filtered.length - 1)));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter" && filtered[selectedIndex]) {
        void handleSelect(filtered[selectedIndex]);
      }
    },
    [onClose, filtered, selectedIndex, handleSelect]
  );

  useEffect(() => {
    const el = listRef.current?.children[selectedIndex];
    if (el instanceof HTMLElement) el.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Quick open"
      className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[15vh]"
      onClick={onClose}
    >
      <div
        className="flex max-h-[400px] w-full max-w-[560px] flex-col overflow-hidden rounded-md bg-card shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          type="text"
          aria-label="Search files"
          placeholder="Search files"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setSelectedIndex(0);
          }}
          onKeyDown={handleKeyDown}
          className="h-10 w-full bg-card px-4 text-sm text-foreground outline-none placeholder:text-foreground/30"
        />
        <div ref={listRef} className="flex-1 overflow-y-auto">
          {error && <div role="alert" className="px-4 py-3 text-xs text-destructive">{error}</div>}
          {searching && <div role="status" className="px-4 py-3 text-xs text-muted-foreground">Searching files…</div>}
          {partial && <div role="status" className="px-4 py-3 text-xs text-muted-foreground">Results are limited; refine your search.</div>}
          {filtered.slice(0, 50).map((file, i) => (
            <div
              key={file.path}
              role="button"
              tabIndex={0}
              onClick={() => void handleSelect(file)}
              onMouseEnter={() => setSelectedIndex(i)}
              className={cn(
                "flex cursor-pointer items-center gap-3 px-4 py-1.5 transition-colors",
                i === selectedIndex && "bg-accent"
              )}
            >
              <FileTypeIcon ext={file.ext} />
              <div className="flex min-w-0 items-baseline gap-2">
                <span className="truncate text-sm text-foreground">{file.name}</span>
                <span className="truncate font-mono text-[11px] text-foreground/30">
                  {file.relativePath}
                </span>
              </div>
            </div>
          ))}
          {!error && !searching && filtered.length === 0 && (
            <div className="px-4 py-6 text-center text-xs text-foreground/30">
              No files found
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const EXT_COLORS: Record<string, string> = {
  ".ts": "text-blue-400",
  ".tsx": "text-blue-400",
  ".js": "text-yellow-400",
  ".jsx": "text-yellow-400",
  ".mjs": "text-yellow-400",
  ".json": "text-amber-300",
  ".yaml": "text-rose-300",
  ".yml": "text-rose-300",
  ".md": "text-foreground/50",
  ".mdx": "text-foreground/50",
  ".css": "text-purple-400",
  ".scss": "text-pink-400",
  ".html": "text-orange-400",
  ".py": "text-green-400",
  ".go": "text-cyan-400",
  ".rs": "text-orange-300",
  ".sh": "text-green-300",
  ".bash": "text-green-300",
  ".sql": "text-sky-300",
  ".svg": "text-amber-400",
};

const EXT_LABELS: Record<string, string> = {
  ".ts": "TS",
  ".tsx": "TX",
  ".js": "JS",
  ".jsx": "JX",
  ".json": "{}",
  ".yaml": "YM",
  ".yml": "YM",
  ".md": "MD",
  ".css": "CS",
  ".py": "PY",
  ".go": "GO",
  ".rs": "RS",
  ".sh": "SH",
  ".html": "<>",
  ".sql": "SQ",
};

export function FileTypeIcon({ ext, size = "sm" }: { ext: string; size?: "sm" | "xs" }) {
  const color = EXT_COLORS[ext] || "text-foreground/30";
  const label = EXT_LABELS[ext] || "";
  const dim = size === "sm" ? "h-4 w-4" : "h-3.5 w-3.5";
  const fontSize = size === "sm" ? "text-[7px]" : "text-[6px]";

  if (label) {
    return (
      <span
        className={cn(
          "flex shrink-0 items-center justify-center font-bold leading-none",
          dim,
          color,
          fontSize
        )}
      >
        {label}
      </span>
    );
  }

  return (
    <span className={cn("flex shrink-0 items-center justify-center", dim, color)}>
      <FileIcon className="h-3 w-3" aria-hidden="true" />
    </span>
  );
}
