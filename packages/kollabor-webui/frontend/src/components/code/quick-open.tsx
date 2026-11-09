import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { File as FileIcon } from "lucide-react";
import { codeFetch, unwrapCodeData } from "@/lib/code-fetch";
import { cn } from "@/lib/utils";
import { useEditorStore } from "@/components/code/editor-store";

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

function fuzzyMatch(query: string, text: string): boolean {
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  let qi = 0;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) qi++;
  }
  return qi === q.length;
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
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const activePaneId = useEditorStore((s) => s.activePaneId);
  const openFile = useEditorStore((s) => s.openFile);
  const pinFile = useEditorStore((s) => s.pinFile);
  const setFileLoading = useEditorStore((s) => s.setFileLoading);

  useEffect(() => {
    if (!open || !workspace) return;
    setQuery("");
    setSelectedIndex(0);
    codeFetch(`/api/fs/tree?workspace=${encodeURIComponent(workspace)}`)
      .then((r) => r.json())
      .then((raw: unknown) => {
        const data = unwrapCodeData<{ tree?: TreeNode[] }>(raw);
        if (data.tree) setFiles(flattenTree(data.tree, workspace));
      })
      .catch(() => {});
    setTimeout(() => inputRef.current?.focus(), 50);
  }, [open, workspace]);

  const filtered = query
    ? files.filter((f) => fuzzyMatch(query, f.relativePath))
    : files;

  const handleSelect = useCallback(
    async (file: FlatFile) => {
      if (!activePaneId) return;
      onClose();
      openFile(activePaneId, file.path, file.name, file.ext, "");
      pinFile(file.path);
      setFileLoading(file.path, true);
      try {
        const res = await codeFetch(`/api/fs/file?workspace=${encodeURIComponent(workspace)}&path=${encodeURIComponent(file.path)}`);
        if (res.ok) {
          const raw: unknown = await res.json();
          const data = unwrapCodeData<{ content?: string }>(raw);
          openFile(activePaneId, file.path, file.name, file.ext, data.content ?? "");
        }
      } catch {
        // a failed read leaves the tab open with empty content, as in mentiko
      } finally {
        setFileLoading(file.path, false);
      }
    },
    [onClose, activePaneId, openFile, pinFile, setFileLoading]
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
        setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
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
          {filtered.length === 0 && (
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
