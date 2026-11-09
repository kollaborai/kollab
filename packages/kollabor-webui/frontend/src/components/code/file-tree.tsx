import { editorShortcutEnabled } from "@/components/code/editor-shortcuts";
import { mutateFileTree } from "@/components/code/file-mutations";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type MouseEvent as ReactMouseEvent,
  type ReactElement,
  type SetStateAction,
} from "react";
import { ChevronsDownUp, FilePlus, Folder, FolderOpen, FolderPlus, Loader2, LocateFixed } from "lucide-react";
import { useEditorStore } from "@/components/code/editor-store";
import { codeFetch, OUTDATED_NOTICE, unwrapCodeData } from "@/lib/code-fetch";
import { DaemonNotice } from "@/components/code/DaemonNotice";
import { cn } from "@/lib/utils";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { FileTypeIcon } from "./quick-open";
import { openWorkspaceFile } from "@/components/code/open-file";
import { LazyFileList } from "@/components/code/lazy-file-list";

interface FileNode {
  name: string;
  path: string;
  type: "file" | "dir";
  children?: FileNode[];
  ext?: string;
}

interface FileTreeProps {
  workspace: string;
  filterOpen?: boolean;
  onFileSelect?: () => void;
}

// Folder accent colors, deterministic by name. Hex values of Tailwind's palette. tab-bar.tsx and
// editor-pane.tsx use getFileAccentColor() as a raw CSS color, so this stays hex until they move too.
const FOLDER_COLORS: Record<string, string> = {
  bin: "#f59e0b",
  chains: "#3b82f6",
  docs: "#22c55e",
  lib: "#38bdf8",
  web: "#06b6d4",
  tests: "#eab308",
  scripts: "#f97316",
  public: "#64748b",
  memory: "#ec4899",
  namespaces: "#8b5cf6",
  agents: "#14b8a6",
  templates: "#f472b6",
  workspace: "#6366f1",
  examples: "#84cc16",
  src: "#06b6d4",
  components: "#3b82f6",
  app: "#22c55e",
  api: "#f59e0b",
  hooks: "#a855f7",
  utils: "#64748b",
  config: "#f97316",
  node_modules: "#374151",
};

// git status letter -> text class (Tailwind palette, the same hues as before)
const GIT_STATUS_CLASSES: Partial<Record<string, string>> = {
  M: "text-amber-500",
  A: "text-green-500",
  D: "text-red-500",
  "?": "text-gray-500",
  R: "text-blue-500",
};

const TOOLBAR_BUTTON =
  "flex size-5 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground";
const INLINE_INPUT =
  "h-5 min-w-0 flex-1 rounded bg-muted/50 px-1.5 font-mono text-[11px] text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-ring";
const MENU_ITEM = "py-1 text-xs";

export function getFolderColor(name: string): string {
  const lower = name.toLowerCase();
  return FOLDER_COLORS[lower] || "#64748b";
}

function relativePath(filePath: string, root: string): string {
  return filePath.startsWith(root) ? filePath.slice(root.length + 1) : filePath;
}

export function getFileAccentColor(filePath: string, rootPath: string): string {
  const firstFolder = relativePath(filePath, rootPath).split("/")[0];
  return getFolderColor(firstFolder);
}

const EXPANDED_KEY = "editor-expanded-folders";

function loadExpanded(workspace: string): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const saved = localStorage.getItem(`${EXPANDED_KEY}-${workspace}`);
    if (saved) return new Set(JSON.parse(saved));
  } catch {}
  return new Set();
}

function saveExpanded(workspace: string, expanded: Set<string>) {
  try {
    localStorage.setItem(`${EXPANDED_KEY}-${workspace}`, JSON.stringify([...expanded]));
  } catch {}
}

// collect all ancestor folder paths for a file path within a tree
function getAncestorPaths(filePath: string, tree: FileNode[]): string[] {
  const result: string[] = [];
  function walk(nodes: FileNode[], path: string[]): boolean {
    for (const node of nodes) {
      if (node.path === filePath) {
        result.push(...path);
        return true;
      }
      if (node.type === "dir" && node.children) {
        if (walk(node.children, [...path, node.path])) return true;
      }
    }
    return false;
  }
  walk(tree, []);
  return result;
}

// The engine's failure envelope: { success: false, error: { code, message } }.
function envelopeError(raw: unknown, fallback: string): string {
  if (typeof raw === "object" && raw !== null && "error" in raw) {
    const { error } = raw;
    if (typeof error === "object" && error !== null && "message" in error && typeof error.message === "string") {
      return error.message;
    }
  }
  return fallback;
}

interface TreeActions {
  createFile: (parentDir: string) => void;
  createFolder: (parentDir: string) => void;
  rename: (path: string, name: string) => void;
  remove: (path: string, name: string) => void;
}

const TreeActionsContext = createContext<TreeActions | null>(null);

function useTreeActions(): TreeActions {
  const actions = useContext(TreeActionsContext);
  if (!actions) throw new Error("File tree rows must render inside FileTree");
  return actions;
}

export function FileTree({ workspace, filterOpen: externalFilterOpen, onFileSelect }: FileTreeProps) {
  const [tree, setTree] = useState<FileNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [largeTree, setLargeTree] = useState(false);
  const [lazyRevision, setLazyRevision] = useState(0);
  const [actionError, setActionError] = useState("");
  const [mutationBusy, setMutationBusy] = useState(false);
  const mutationInFlight = useRef(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [initialized, setInitialized] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [panelHeights, setPanelHeights] = useState<Map<string, number>>(new Map());
  const [gitStatus, setGitStatus] = useState<Record<string, string>>({});
  const [gitStatusError, setGitStatusError] = useState("");
  const gitStatusSeq = useRef(0);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const treeRef = useRef<HTMLDivElement>(null);
  const filterOpen = externalFilterOpen ?? false;

  const activePaneId = useEditorStore((s) => s.activePaneId);
  const panes = useEditorStore((s) => s.panes);
  const pinFile = useEditorStore((s) => s.pinFile);
  const setActiveFile = useEditorStore((s) => s.setActiveFile);

  const activePane = panes.find((p) => p.id === activePaneId);
  const activeFilePath = activePane?.activePath ?? null;

  // hydrate expanded state from localStorage
  useEffect(() => {
    const saved = loadExpanded(workspace);
    if (saved.size > 0) setExpanded(saved);
  }, [workspace]);

  // persist expanded state
  useEffect(() => {
    if (initialized) saveExpanded(workspace, expanded);
  }, [expanded, workspace, initialized]);

  const fetchTree = useCallback(async () => {
    setLoading(true);
    setError("");
    setLargeTree(false);
    try {
      const res = await codeFetch(`/api/fs/tree?workspace=${encodeURIComponent(workspace)}`);
      const raw: unknown = await res.json();
      if (!res.ok) {
        if (typeof raw === "object" && raw !== null && "error" in raw &&
            typeof raw.error === "object" && raw.error !== null && "code" in raw.error && raw.error.code === "tree_too_large") {
          setLargeTree(true);
          return;
        }
        setError(envelopeError(raw, "Failed to load"));
        return;
      }
      const data = unwrapCodeData<{ tree?: FileNode[] }>(raw);
      setTree(data.tree || []);
      if (!initialized) setInitialized(true);
    } catch {
      setError("Failed to load file tree");
    } finally {
      setLoading(false);
    }
  }, [workspace, initialized]);

  useEffect(() => {
    fetchTree();
  }, [fetchTree]);

  const toggleExpand = useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const collapseAll = useCallback(() => {
    setExpanded(new Set());
  }, []);

  const revealActiveFile = useCallback(() => {
    if (!activeFilePath || tree.length === 0) return;
    const ancestors = getAncestorPaths(activeFilePath, tree);
    if (ancestors.length > 0) {
      setExpanded((prev) => {
        const next = new Set(prev);
        for (const p of ancestors) next.add(p);
        return next;
      });
    }
    // scroll to active file after state update
    requestAnimationFrame(() => {
      const el = treeRef.current?.querySelector('[data-active="true"]');
      el?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
  }, [activeFilePath, tree]);

  const handleFileClick = useCallback(
    async (node: FileNode) => {
      setSelectedPath(node.path);

      if (node.type === "dir") {
        toggleExpand(node.path);
        return;
      }

      if (!activePaneId) return;

      const alreadyInPane = activePane?.openPaths.includes(node.path);
      if (alreadyInPane) {
        setActiveFile(activePaneId, node.path);
        onFileSelect?.();
        return;
      }

      onFileSelect?.();
      await openWorkspaceFile(workspace, node.path);
    },
    [activePaneId, activePane, onFileSelect, setActiveFile, toggleExpand, workspace]
  );

  const handleFileDoubleClick = useCallback(
    (node: FileNode) => {
      if (node.type === "file") pinFile(node.path);
    },
    [pinFile]
  );

  // flattened, keyboard-navigable order of everything currently visible
  const flatRows = useMemo(() => {
    const dirs = tree.filter((n) => n.type === "dir");
    const files = tree.filter((n) => n.type === "file");
    return flattenVisibleFileRows(dirs, files, expanded, searchQuery);
  }, [tree, expanded, searchQuery]);

  // path -> node lookup, so keyboard nav can re-check expand/search state
  // the same way the render path does (via isDirExpanded), without duplicating it
  const nodesByPath = useMemo(() => {
    const map = new Map<string, FileNode>();
    function walk(nodes: FileNode[]) {
      for (const node of nodes) {
        map.set(node.path, node);
        if (node.children) walk(node.children);
      }
    }
    walk(tree);
    return map;
  }, [tree]);

  // keyboard nav: up/down move selection, left/right collapse/expand or step to parent/child
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      if (!editorShortcutEnabled() || !target || !treeRef.current?.contains(target) || !target.closest("[data-file-row-id]")) return;
      const tag = target?.tagName;
      if (tag === "TEXTAREA" || tag === "INPUT") return;
      // an open context menu owns the arrow keys
      if (target?.closest?.('[role="menu"]')) return;
      if (!["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"].includes(e.key)) return;
      if (flatRows.length === 0) return;

      const focusedPath = target.closest("[data-file-row-id]")?.getAttribute("data-file-row-id");
      const currentIndex = flatRows.findIndex((row) => row.path === focusedPath);
      const selectRow = (path: string) => {
        setSelectedPath(path);
        treeRef.current?.querySelector<HTMLElement>(`[data-file-row-id="${CSS.escape(path)}"]`)?.focus();
      };

      if (e.key === "ArrowDown") {
        e.preventDefault();
        const nextIndex = currentIndex === -1 ? 0 : Math.min(currentIndex + 1, flatRows.length - 1);
        selectRow(flatRows[nextIndex].path);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        const prevIndex = currentIndex <= 0 ? 0 : currentIndex - 1;
        selectRow(flatRows[prevIndex].path);
      } else if (e.key === "ArrowRight") {
        if (currentIndex === -1) return;
        const row = flatRows[currentIndex];
        if (row.type !== "dir" || !row.hasChildren) return;
        e.preventDefault();
        const node = nodesByPath.get(row.path);
        const isOpen = node ? isDirExpanded(node, expanded, searchQuery) : false;
        if (!isOpen) {
          toggleExpand(row.path);
        } else {
          const child = flatRows[currentIndex + 1];
          if (child?.parentPath === row.path) selectRow(child.path);
        }
      } else if (e.key === "ArrowLeft") {
        if (currentIndex === -1) return;
        const row = flatRows[currentIndex];
        const node = row.type === "dir" ? nodesByPath.get(row.path) : undefined;
        const isOpen = node ? isDirExpanded(node, expanded, searchQuery) : false;
        if (row.type === "dir" && row.hasChildren && isOpen) {
          e.preventDefault();
          toggleExpand(row.path);
        } else if (row.parentPath) {
          e.preventDefault();
          selectRow(row.parentPath);
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [flatRows, selectedPath, expanded, searchQuery, toggleExpand, nodesByPath]);

  // keep the selected row scrolled into view (keyboard nav can move selection off-screen)
  useEffect(() => {
    if (!selectedPath) return;
    const el = treeRef.current?.querySelector(
      `[data-file-row-id="${CSS.escape(selectedPath)}"]`
    );
    el?.scrollIntoView({ block: "nearest" });
  }, [selectedPath]);

  // follow the active file so keyboard nav starts from wherever the editor is
  // (e.g. a file opened via quick-open or a tab click, not just this tree)
  useEffect(() => {
    if (activeFilePath) setSelectedPath(activeFilePath);
  }, [activeFilePath]);

  // inline create state
  const [inlineCreate, setInlineCreate] = useState<{ parentDir: string; type: "file" | "dir" } | null>(null);
  const inlineRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (inlineCreate) {
      // focus on next tick after render
      requestAnimationFrame(() => inlineRef.current?.focus());
    }
  }, [inlineCreate]);

  const refreshGitStatus = useCallback(() => {
    if (!workspace) return;
    const seq = ++gitStatusSeq.current;
    codeFetch(`/api/fs/git-status?workspace=${encodeURIComponent(workspace)}`)
      .then((r) => r.json())
      .then((raw: unknown) => {
        const data = unwrapCodeData<{ status?: Record<string, string> }>(raw);
        if (seq !== gitStatusSeq.current) return;
        setGitStatus(data.status ?? {});
        setGitStatusError("");
      })
      .catch((reason: unknown) => {
        if (seq !== gitStatusSeq.current) return;
        setGitStatus({});
        setGitStatusError(reason instanceof Error ? reason.message : "Git status is unavailable.");
      });
  }, [workspace]);

  // fetch git status on mount
  useEffect(() => {
    setGitStatus({});
    setGitStatusError("");
    refreshGitStatus();
    return () => { gitStatusSeq.current += 1; };
  }, [refreshGitStatus]);

  const commitInlineCreate = useCallback(async (name: string) => {
    if (mutationInFlight.current) return;
    if (!inlineCreate || !name.trim()) {
      setInlineCreate(null);
      return;
    }
    const path = `${inlineCreate.parentDir}/${name.trim()}`;
    mutationInFlight.current = true;
    setMutationBusy(true);
    setActionError("");
    try {
      await mutateFileTree("create", { workspace, path, type: inlineCreate.type });
      setInlineCreate(null);
      if (largeTree) setLazyRevision((value) => value + 1); else fetchTree();
      refreshGitStatus();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Could not create the path.");
    } finally {
      mutationInFlight.current = false;
      setMutationBusy(false);
    }
  }, [inlineCreate, workspace, fetchTree, refreshGitStatus, largeTree]);

  const handleCreateFile = useCallback((parentDir: string) => {
    if (mutationInFlight.current) return;
    setActionError("");
    setInlineCreate({ parentDir, type: "file" });
    // expand parent so the input is visible
    if (parentDir !== workspace) {
      setExpanded((prev) => new Set([...prev, parentDir]));
    }
  }, [workspace]);

  const handleCreateFolder = useCallback((parentDir: string) => {
    if (mutationInFlight.current) return;
    setActionError("");
    setInlineCreate({ parentDir, type: "dir" });
    if (parentDir !== workspace) {
      setExpanded((prev) => new Set([...prev, parentDir]));
    }
  }, [workspace]);

  // inline rename state
  const [inlineRename, setInlineRename] = useState<{ path: string; name: string } | null>(null);
  const renameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (inlineRename) requestAnimationFrame(() => {
      renameRef.current?.focus();
      // select the name without extension for convenience
      const dot = inlineRename.name.lastIndexOf(".");
      renameRef.current?.setSelectionRange(0, dot > 0 ? dot : inlineRename.name.length);
    });
  }, [inlineRename]);

  const commitRename = useCallback(async (newName: string) => {
    if (mutationInFlight.current) return;
    if (!inlineRename || !newName.trim() || newName.trim() === inlineRename.name) {
      setInlineRename(null);
      return;
    }
    const dir = inlineRename.path.slice(0, inlineRename.path.length - inlineRename.name.length);
    const newPath = `${dir}${newName.trim()}`;
    mutationInFlight.current = true;
    setMutationBusy(true);
    setActionError("");
    try {
      await mutateFileTree("rename", { workspace, oldPath: inlineRename.path, newPath });
      setInlineRename(null);
      if (largeTree) setLazyRevision((value) => value + 1); else fetchTree();
      refreshGitStatus();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Could not rename the path.");
    } finally {
      mutationInFlight.current = false;
      setMutationBusy(false);
    }
  }, [inlineRename, workspace, fetchTree, refreshGitStatus, largeTree]);

  const handleRename = useCallback((oldPath: string, currentName: string) => {
    if (mutationInFlight.current) return;
    setActionError("");
    setInlineRename({ path: oldPath, name: currentName });
  }, []);

  // inline delete confirmation
  const [deleteConfirm, setDeleteConfirm] = useState<{ path: string; name: string } | null>(null);

  const handleDelete = useCallback((path: string, name: string) => {
    if (mutationInFlight.current) return;
    setActionError("");
    setDeleteConfirm({ path, name });
  }, []);

  const confirmDelete = useCallback(async () => {
    if (!deleteConfirm || mutationInFlight.current) return;
    mutationInFlight.current = true;
    setMutationBusy(true);
    setActionError("");
    try {
      await mutateFileTree("delete", { workspace, path: deleteConfirm.path });
      setDeleteConfirm(null);
      if (largeTree) setLazyRevision((value) => value + 1); else fetchTree();
      refreshGitStatus();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Could not delete the path.");
    } finally {
      mutationInFlight.current = false;
      setMutationBusy(false);
    }
  }, [deleteConfirm, workspace, fetchTree, refreshGitStatus, largeTree]);

  const treeActions = useMemo<TreeActions>(
    () => ({
      createFile: handleCreateFile,
      createFolder: handleCreateFolder,
      rename: handleRename,
      remove: handleDelete,
    }),
    [handleCreateFile, handleCreateFolder, handleRename, handleDelete]
  );

  // focus filter input when opened
  useEffect(() => {
    if (filterOpen) filterRef.current?.focus();
  }, [filterOpen]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="size-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return (
      error === OUTDATED_NOTICE ? (
        <DaemonNotice />
      ) : (
        <div className="px-3 py-3 text-xs text-muted-foreground">{error}</div>
      )
    );
  }

  // separate dirs and files at root level
  const dirs = tree.filter((n) => n.type === "dir");
  const files = tree.filter((n) => n.type === "file");

  return (
    <TreeActionsContext.Provider value={treeActions}>
      <div className="flex h-full flex-col overflow-hidden">
        {/* filter input - controlled by icon strip */}
        {filterOpen && (
          <div className="shrink-0 px-2 pb-1 pt-1 animate-in slide-in-from-top-1 duration-150">
            <input
              ref={filterRef}
              type="text"
              placeholder="filter..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Escape") setSearchQuery(""); }}
              className="h-5 w-full rounded-md bg-muted/40 px-2 font-mono text-[10px] text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-ring"
            />
          </div>
        )}

        {/* tree toolbar */}
        <div className="flex shrink-0 items-center gap-0.5 px-2 py-0.5">
          <button type="button" onClick={() => handleCreateFile(workspace)} className={TOOLBAR_BUTTON} title="New file">
            <FilePlus className="size-3.5" />
          </button>
          <button type="button" onClick={() => handleCreateFolder(workspace)} className={TOOLBAR_BUTTON} title="New folder">
            <FolderPlus className="size-3.5" />
          </button>
          <button type="button" onClick={collapseAll} className={TOOLBAR_BUTTON} title="Collapse all">
            <ChevronsDownUp className="size-3.5" />
          </button>
          {activeFilePath && (
            <button type="button" onClick={revealActiveFile} className={TOOLBAR_BUTTON} title="Reveal active file">
              <LocateFixed className="size-3.5" />
            </button>
          )}
        </div>

        {/* accordion folders */}
        <div ref={treeRef} className="flex-1 overflow-y-auto px-1 pb-1">
          {actionError && <div role="alert" className="px-2 py-1 text-xs text-destructive">{actionError}</div>}
          {gitStatusError && <div role="alert" className="px-2 py-1 text-xs text-destructive">Git status unavailable: {gitStatusError}</div>}
          {/* inline delete confirmation */}
          {deleteConfirm && (
            <div className="mb-0.5 flex items-center gap-1.5 rounded bg-destructive/10 px-2 py-1">
              <span className="flex-1 truncate text-[10px] text-destructive">delete {deleteConfirm.name}?</span>
              <button
                type="button"
                onClick={confirmDelete}
                disabled={mutationBusy}
                className="rounded bg-destructive/20 px-2 py-0.5 font-mono text-[10px] text-destructive transition-colors hover:bg-destructive/30"
                autoFocus
              >
                yes
              </button>
              <button
                type="button"
                disabled={mutationBusy}
                onClick={() => setDeleteConfirm(null)}
                className="rounded px-2 py-0.5 font-mono text-[10px] text-muted-foreground transition-colors hover:bg-accent"
              >
                no
              </button>
            </div>
          )}

          {/* inline rename input */}
          {inlineRename && (
            <div className="mb-0.5 flex items-center gap-1.5 px-2 py-0.5">
              <span className="shrink-0 text-[10px] text-muted-foreground">rename</span>
              <input
                ref={renameRef}
                disabled={mutationBusy}
                type="text"
                defaultValue={inlineRename.name}
                className={INLINE_INPUT}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    commitRename((e.target as HTMLInputElement).value);
                  } else if (e.key === "Escape" && !mutationInFlight.current) {
                    setInlineRename(null);
                  }
                }}
                onBlur={(e) => commitRename(e.target.value)}
              />
            </div>
          )}

          {/* inline create input */}
          {inlineCreate && (
            <div className="mb-0.5 flex items-center gap-1.5 px-2 py-0.5">
              {inlineCreate.type === "dir" ? (
                <Folder className="size-3 shrink-0 fill-current text-muted-foreground" />
              ) : (
                <span className="flex size-3 shrink-0 items-center justify-center text-[9px] text-muted-foreground">F</span>
              )}
              <input
                ref={inlineRef}
                disabled={mutationBusy}
                type="text"
                placeholder={inlineCreate.type === "dir" ? "folder name..." : "file name..."}
                className={INLINE_INPUT}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    commitInlineCreate((e.target as HTMLInputElement).value);
                  } else if (e.key === "Escape" && !mutationInFlight.current) {
                    setInlineCreate(null);
                  }
                }}
                onBlur={(e) => {
                  // commit on blur if there's a value, otherwise cancel
                  const val = e.target.value.trim();
                  if (val) {
                    commitInlineCreate(val);
                  } else {
                    setInlineCreate(null);
                  }
                }}
              />
            </div>
          )}

          {largeTree && <LazyFileList key={workspace} workspace={workspace} onFileSelect={onFileSelect}
            revision={lazyRevision} gitStatus={gitStatus} filter={searchQuery} onCreateFile={handleCreateFile}
            onCreateFolder={handleCreateFolder} onRename={handleRename} onDelete={handleDelete} />}
          {!largeTree && dirs.map((dir) => (
            <AccordionFolder
              key={dir.path}
              node={dir}
              depth={0}
              expanded={expanded}
              activeFilePath={activeFilePath}
              selectedPath={selectedPath}
              searchQuery={searchQuery}
              panelHeights={panelHeights}
              setPanelHeights={setPanelHeights}
              gitStatus={gitStatus}
              workspace={workspace}
              onClick={handleFileClick}
              onDoubleClick={handleFileDoubleClick}
            />
          ))}

          {/* root-level files */}
          {!largeTree && files.length > 0 && (
            <div className="mt-1">
              {files
                .filter((f) => matchesSearch(f, searchQuery))
                .map((file) => (
                  <FileItem
                    key={file.path}
                    node={file}
                    isActive={activeFilePath === file.path}
                    isSelected={selectedPath === file.path}
                    gitIndicator={gitStatus[relativePath(file.path, workspace)]}
                    onClick={handleFileClick}
                    onDoubleClick={handleFileDoubleClick}
                  />
                ))}
            </div>
          )}
        </div>
      </div>
    </TreeActionsContext.Provider>
  );
}

// Right-click (long press on touch) menu for one row. Radix handles open, close and Escape.
function NodeMenu({ node, children }: { node: FileNode; children: ReactElement }) {
  const actions = useTreeActions();
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      <ContextMenuContent
        data-editor-overlay
        // rename and create focus their input on the next frame; the menu must not hand focus back to the row first
        onCloseAutoFocus={(event) => event.preventDefault()}
      >
        {node.type === "dir" && (
          <>
            <ContextMenuItem className={MENU_ITEM} onSelect={() => actions.createFile(node.path)}>
              New file
            </ContextMenuItem>
            <ContextMenuItem className={MENU_ITEM} onSelect={() => actions.createFolder(node.path)}>
              New folder
            </ContextMenuItem>
            <ContextMenuSeparator />
          </>
        )}
        <ContextMenuItem className={MENU_ITEM} onSelect={() => actions.rename(node.path, node.name)}>
          Rename
        </ContextMenuItem>
        <ContextMenuItem
          className={MENU_ITEM}
          variant="destructive"
          onSelect={() => actions.remove(node.path, node.name)}
        >
          Delete
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  );
}

function matchesSearch(node: FileNode, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  if (node.name.toLowerCase().includes(q)) return true;
  if (node.type === "dir" && node.children) {
    return node.children.some((child) => matchesSearch(child, q));
  }
  return false;
}

// should this dir's children currently be shown (manually expanded, or
// auto-expanded because a search filter matched something inside it)?
function isDirExpanded(node: FileNode, expanded: Set<string>, searchQuery: string): boolean {
  const manuallyOpen = expanded.has(node.path);
  const autoExpand = searchQuery.length > 0 && matchesSearch(node, searchQuery);
  return manuallyOpen || autoExpand;
}

interface FlatFileRow {
  path: string;
  parentPath: string | null;
  type: "file" | "dir";
  hasChildren: boolean;
}

// flatten the visible tree (respecting collapse + search filter) into
// keyboard-nav order. mirrors the same dirs-then-files, filtered ordering
// the render path uses (dirs.map / childDirs.map / childFiles.map).
function flattenVisibleFileRows(
  dirs: FileNode[],
  files: FileNode[],
  expanded: Set<string>,
  searchQuery: string
): FlatFileRow[] {
  const rows: FlatFileRow[] = [];

  function walkDir(node: FileNode, parentPath: string | null) {
    if (searchQuery && !matchesSearch(node, searchQuery)) return;
    const childDirs = node.children?.filter((c) => c.type === "dir") ?? [];
    const childFiles = node.children?.filter((c) => c.type === "file") ?? [];
    const hasChildren = childDirs.length > 0 || childFiles.length > 0;
    rows.push({ path: node.path, parentPath, type: "dir", hasChildren });
    if (hasChildren && isDirExpanded(node, expanded, searchQuery)) {
      for (const dir of childDirs) walkDir(dir, node.path);
      for (const file of childFiles) {
        if (matchesSearch(file, searchQuery)) {
          rows.push({ path: file.path, parentPath: node.path, type: "file", hasChildren: false });
        }
      }
    }
  }

  for (const dir of dirs) walkDir(dir, null);
  for (const file of files) {
    if (matchesSearch(file, searchQuery)) {
      rows.push({ path: file.path, parentPath: null, type: "file", hasChildren: false });
    }
  }

  return rows;
}

// ── accordion folder ──

interface AccordionFolderProps {
  node: FileNode;
  depth: number;
  expanded: Set<string>;
  activeFilePath: string | null;
  selectedPath: string | null;
  searchQuery: string;
  panelHeights: Map<string, number>;
  setPanelHeights: Dispatch<SetStateAction<Map<string, number>>>;
  gitStatus: Record<string, string>;
  workspace: string;
  onClick: (node: FileNode) => void;
  onDoubleClick: (node: FileNode) => void;
}

function AccordionFolder({
  node,
  depth,
  expanded,
  activeFilePath,
  selectedPath,
  searchQuery,
  panelHeights,
  setPanelHeights,
  gitStatus,
  workspace,
  onClick,
  onDoubleClick,
}: AccordionFolderProps) {
  const isOpen = isDirExpanded(node, expanded, searchQuery);
  const isSelected = selectedPath === node.path;
  const color = getFolderColor(node.name);
  const childCount = node.children?.length ?? 0;
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);

  if (searchQuery && !matchesSearch(node, searchQuery)) return null;

  const childDirs = node.children?.filter((c) => c.type === "dir") ?? [];
  const childFiles = node.children?.filter((c) => c.type === "file") ?? [];

  // only constrain height on top-level folders (depth 0)
  // nested folders flow naturally within their parent
  const isTopLevel = depth === 0;
  const defaultMaxH = Math.min(Math.max(childCount * 26, 60), 200);
  const maxH = panelHeights.get(node.path) ?? defaultMaxH;

  const handleResizeStart = (e: ReactMouseEvent) => {
    if (!isTopLevel) return;
    e.preventDefault();
    e.stopPropagation();
    dragRef.current = { startY: e.clientY, startH: maxH };

    const onMove = (me: globalThis.MouseEvent) => {
      if (!dragRef.current) return;
      const delta = me.clientY - dragRef.current.startY;
      const newH = Math.max(40, dragRef.current.startH + delta);
      setPanelHeights((prev) => {
        const next = new Map(prev);
        next.set(node.path, newH);
        return next;
      });
    };

    const onUp = () => {
      dragRef.current = null;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  return (
    <div className="mb-0.5">
      {/* folder header */}
      <NodeMenu node={node}>
        <button
          type="button"
          data-file-row-id={node.path}
          onClick={() => onClick(node)}
          className={cn(
            "group flex w-full items-center gap-2 rounded-md py-0.5 pr-2 pl-2 transition-colors hover:bg-accent/50",
            isSelected && "bg-accent"
          )}
        >
          <span className="shrink-0" style={{ color }}>
            {isOpen
              ? <FolderOpen className="size-3.5 fill-current" />
              : <Folder className="size-3.5 fill-current" />
            }
          </span>
          <span className="truncate font-mono text-[11px] text-foreground/80 transition-colors group-hover:text-foreground">
            {node.name}
          </span>
          <span className="ml-auto shrink-0 font-mono text-[9px] text-muted-foreground/60">{childCount}</span>
        </button>
      </NodeMenu>

      {/* expanded content */}
      {isOpen && (
        <div className="mx-1 mt-0.5">
          <div
            className={isTopLevel ? "overflow-y-auto overflow-x-hidden rounded-t-md" : "overflow-x-hidden"}
            style={isTopLevel ? { maxHeight: maxH } : undefined}
          >
            {childDirs.map((dir) => (
              <AccordionFolder
                key={dir.path}
                node={dir}
                depth={depth + 1}
                expanded={expanded}
                activeFilePath={activeFilePath}
                selectedPath={selectedPath}
                searchQuery={searchQuery}
                panelHeights={panelHeights}
                setPanelHeights={setPanelHeights}
                gitStatus={gitStatus}
                workspace={workspace}
                onClick={onClick}
                onDoubleClick={onDoubleClick}
              />
            ))}
            {childFiles
              .filter((f) => matchesSearch(f, searchQuery))
              .map((file) => (
                <FileItem
                  key={file.path}
                  node={file}
                  isActive={activeFilePath === file.path}
                  isSelected={selectedPath === file.path}
                  gitIndicator={gitStatus[relativePath(file.path, workspace)]}
                  onClick={onClick}
                  onDoubleClick={onDoubleClick}
                />
              ))}
          </div>
          {/* resize handle - only for top-level folders */}
          {isTopLevel && (
            <div
              onMouseDown={handleResizeStart}
              className="flex h-1.5 cursor-row-resize items-center justify-center rounded-b-md transition-colors hover:bg-accent/50"
            >
              <div className="h-px w-8 bg-muted-foreground/30" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── file item ──

interface FileItemProps {
  node: FileNode;
  isActive: boolean;
  isSelected?: boolean;
  gitIndicator?: string;
  onClick: (node: FileNode) => void;
  onDoubleClick: (node: FileNode) => void;
}

function FileItem({ node, isActive, isSelected, gitIndicator, onClick, onDoubleClick }: FileItemProps) {
  const gitClass = gitIndicator ? GIT_STATUS_CLASSES[gitIndicator] : undefined;

  return (
    <NodeMenu node={node}>
      <div
        role="button"
        tabIndex={0}
        data-file-row-id={node.path}
        onClick={() => onClick(node)}
        onDoubleClick={() => onDoubleClick(node)}
        onKeyDown={(e) => { if (e.key === "Enter") onClick(node); }}
        data-active={isActive || undefined}
        className={cn(
          "relative flex cursor-pointer items-center gap-1.5 rounded-sm px-2 py-1 transition-colors",
          isActive ? "bg-accent" : isSelected ? "bg-accent/50" : "hover:bg-accent/50"
        )}
      >
        {isActive && (
          <span className="absolute top-1.5 bottom-1.5 left-0.5 w-[2px] rounded-r bg-primary" />
        )}
        <span className="shrink-0">
          <FileTypeIcon ext={node.ext || ""} size="xs" />
        </span>
        <span
          className={cn(
            "truncate font-mono text-[11px]",
            gitClass ?? (isActive ? "text-foreground" : "text-foreground/80")
          )}
        >
          {node.name}
        </span>
        {gitIndicator && (
          <span className={cn("ml-auto shrink-0 font-mono text-[8px] font-bold", gitClass)}>
            {gitIndicator}
          </span>
        )}
      </div>
    </NodeMenu>
  );
}
