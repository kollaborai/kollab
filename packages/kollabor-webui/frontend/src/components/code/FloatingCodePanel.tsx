import { useEffect, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { Files, Filter, GitBranch, PanelLeft, Search, Settings } from "lucide-react";
import { Button } from "@/components/ui/button";
import { codeFetch, unwrapCodeData } from "@/lib/code-fetch";
import { FLOATING_SURFACE_Z } from "@/lib/floating-surface-z";
import { cn } from "@/lib/utils";
import { EditorConfigPanel } from "@/components/code/editor-config";
import { type FileData, useEditorStore } from "@/components/code/editor-store";
import { FileTree } from "@/components/code/file-tree";
import { FloatingFileSidebar, useFileSidebarVisibility } from "@/components/code/floating-sidebar";
import { FloatingWindowManager } from "@/components/code/floating-window-manager";
import { GitPanel } from "@/components/code/git/GitPanel";
import { QuickOpen } from "@/components/code/quick-open";
import { SearchPanel } from "@/components/code/search-panel";
import { SplitContainer } from "@/components/code/split-container";

export interface FloatingCodePanelProps {
  workspace: string;
  onClose: () => void;
}

const GEOMETRY_KEY = "kollab:floating-code-geometry:v1";
// the folder the saved editor tabs were opened in; a reload into another folder starts with no tabs
const TABS_WORKSPACE_KEY = "kollab:code-editor-tabs-workspace:v1";

function savedTabsWorkspace(): string | null {
  try {
    return window.localStorage.getItem(TABS_WORKSPACE_KEY);
  } catch {
    return null;
  }
}

function rememberTabsWorkspace(workspace: string): void {
  try {
    window.localStorage.setItem(TABS_WORKSPACE_KEY, workspace);
  } catch {
    // storage blocked or full: the tabs are not tied to a folder across a reload
  }
}

/** Closes every tab and split, keeping the first pane. The editor store has no reset action. */
function resetEditorTabs(): void {
  const first = useEditorStore.getState().panes[0];
  if (!first) return;
  useEditorStore.setState({
    panes: [{ id: first.id, openPaths: [], activePath: null }],
    activePaneId: first.id,
    splitTree: { type: "leaf", paneId: first.id },
    fileCache: new Map<string, FileData>(),
  });
}

function fileNameOf(absPath: string): string {
  return absPath.slice(absPath.lastIndexOf("/") + 1);
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

/** The file's text, or null when it cannot be read. Same route quick open reads through. */
async function readFileContent(workspace: string, absPath: string): Promise<string | null> {
  try {
    const res = await codeFetch(`/api/fs/file?workspace=${encodeURIComponent(workspace)}&path=${encodeURIComponent(absPath)}`);
    if (!res.ok) return null;
    const raw: unknown = await res.json();
    return unwrapCodeData<{ content?: string }>(raw).content ?? "";
  } catch {
    return null;
  }
}

/** Opens a file in the active pane (git panel's "open file"). */
async function openFileInActivePane(workspace: string, absPath: string): Promise<void> {
  const store = useEditorStore.getState();
  const paneId = store.activePaneId;
  const name = fileNameOf(absPath);
  const ext = extensionOf(name);
  const pane = store.panes.find((p) => p.id === paneId);
  if (pane?.openPaths.includes(absPath)) {
    store.setActiveFile(paneId, absPath);
    return;
  }
  const cached = store.fileCache.get(absPath);
  if (cached) {
    // already loaded, maybe with unsaved edits: show that buffer, never reload over it
    store.openFile(paneId, absPath, name, ext, cached.content);
    return;
  }
  // the tab shows at once and fills in when the read lands, as quick open does
  store.openFile(paneId, absPath, name, ext, "");
  store.pinFile(absPath);
  store.setFileLoading(absPath, true);
  const content = await readFileContent(workspace, absPath);
  if (content === null) {
    // a failed read must not leave an empty tab, which a save would write over the file
    useEditorStore.getState().closeFile(paneId, absPath);
    return;
  }
  useEditorStore.getState().openFile(paneId, absPath, name, ext, content);
}

/** Opens a diff tab: working copy against the HEAD content the git panel passes (null = new file). */
async function openDiffInActivePane(workspace: string, absPath: string, headContent: string | null): Promise<void> {
  const name = fileNameOf(absPath);
  const cached = useEditorStore.getState().fileCache.get(absPath);
  const modified = cached ? cached.content : await readFileContent(workspace, absPath);
  if (modified === null) return;
  const store = useEditorStore.getState();
  store.openDiffFile(store.activePaneId, absPath, name, extensionOf(name), modified, headContent);
}

function SidebarToggle({
  label,
  active,
  onClick,
  children,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      aria-label={label}
      aria-pressed={active}
      title={label}
      className={cn(active ? "bg-accent text-accent-foreground" : "text-muted-foreground")}
      onClick={onClick}
    >
      {children}
    </Button>
  );
}

/** The floating code editor window for one chat folder: file sidebar, split editor panes, quick open. */
export function FloatingCodePanel({ workspace, onClose }: FloatingCodePanelProps) {
  const sidebarView = useEditorStore((s) => s.sidebarView);
  const setSidebarView = useEditorStore((s) => s.setSidebarView);
  const [quickOpenOpen, setQuickOpenOpen] = useState(false);
  const [fileFilterOpen, setFileFilterOpen] = useState(false);
  const sidebar = useFileSidebarVisibility();

  // tabs opened in another folder are dropped; the folder reaches each sibling as the `workspace` prop
  useEffect(() => {
    const owner = savedTabsWorkspace();
    if (owner !== null && owner !== workspace) resetEditorTabs();
    rememberTabsWorkspace(workspace);
  }, [workspace]);

  // Tabs come back from storage as paths only. Read each one that has no text yet; a file that cannot be read
  // closes its tab, since an empty tab saved over the file would erase it. Runs after the folder check above.
  useEffect(() => {
    let cancelled = false;
    const { panes, fileCache } = useEditorStore.getState();
    for (const pane of panes) {
      for (const path of pane.openPaths) {
        if (fileCache.has(path)) continue;
        void readFileContent(workspace, path).then((content) => {
          if (cancelled) return;
          const store = useEditorStore.getState();
          if (store.fileCache.has(path)) return;
          if (content === null) {
            store.closeFile(pane.id, path);
            return;
          }
          const name = fileNameOf(path);
          const active = store.panes.find((p) => p.id === pane.id)?.activePath ?? null;
          store.openFile(pane.id, path, name, extensionOf(name), content);
          if (active && active !== path) store.setActiveFile(pane.id, active);
        });
      }
    }
    return () => {
      cancelled = true;
    };
  }, [workspace]);

  // cmd/ctrl+p toggles quick open, cmd/ctrl+shift+f toggles the search view
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      if (e.key === "p") {
        e.preventDefault();
        setQuickOpenOpen((open) => !open);
      } else if (e.shiftKey && e.key.toLowerCase() === "f") {
        e.preventDefault();
        useEditorStore.getState().toggleSearchPanel();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Escape closes quick open first; the window closes only when no overlay is open
  const guardEscape = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Escape" || !quickOpenOpen) return;
    e.preventDefault();
    setQuickOpenOpen(false);
  };

  const folderLabel = workspace.split("/").filter(Boolean).slice(-2).join("/");

  return (
    <FloatingWindowManager
      storageKey={GEOMETRY_KEY}
      zIndex={FLOATING_SURFACE_Z.codePanel}
      title={
        <span className="flex min-w-0 items-center gap-1.5">
          {sidebar.narrow && (
            <span className="flex shrink-0">
              <SidebarToggle label="Files sidebar" active={sidebar.visible} onClick={sidebar.toggle}>
                <PanelLeft className="h-4 w-4" />
              </SidebarToggle>
            </span>
          )}
          <span className="min-w-0 truncate">
            Code <span className="font-mono text-[10px] font-normal text-muted-foreground">{folderLabel}</span>
          </span>
        </span>
      }
      onClose={onClose}
      dataAttr={{ "data-floating-code-panel": "" }}
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden" onKeyDown={guardEscape}>
        <div className="relative flex min-h-0 flex-1 gap-2 overflow-hidden p-2">
          <FloatingFileSidebar visible={sidebar.visible} narrow={sidebar.narrow}>
            <div className="flex shrink-0 items-center gap-0.5 px-3 py-1">
              <SidebarToggle label="Explorer" active={sidebarView === "files"} onClick={() => setSidebarView("files")}>
                <Files className="h-4 w-4" />
              </SidebarToggle>
              <SidebarToggle label="Search" active={sidebarView === "search"} onClick={() => setSidebarView("search")}>
                <Search className="h-4 w-4" />
              </SidebarToggle>
              <SidebarToggle label="Settings" active={sidebarView === "config"} onClick={() => setSidebarView("config")}>
                <Settings className="h-4 w-4" />
              </SidebarToggle>
              <SidebarToggle label="Filter Files" active={fileFilterOpen} onClick={() => setFileFilterOpen((v) => !v)}>
                <Filter className="h-4 w-4" />
              </SidebarToggle>
              <SidebarToggle label="Source Control" active={sidebarView === "git"} onClick={() => setSidebarView("git")}>
                <GitBranch className="h-4 w-4" />
              </SidebarToggle>
            </div>

            {sidebarView === "files" && <FileTree workspace={workspace} filterOpen={fileFilterOpen} />}
            {sidebarView === "search" && <SearchPanel workspace={workspace} />}
            {sidebarView === "config" && <EditorConfigPanel />}
            {sidebarView === "git" && (
              <GitPanel
                workspace={workspace}
                onOpenFile={(absPath) => void openFileInActivePane(workspace, absPath)}
                onOpenDiff={(absPath, headContent) => void openDiffInActivePane(workspace, absPath, headContent)}
              />
            )}
          </FloatingFileSidebar>

          <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
            <SplitContainer workspace={workspace} />
          </div>
        </div>

        <QuickOpen open={quickOpenOpen} onClose={() => setQuickOpenOpen(false)} workspace={workspace} />
      </div>
    </FloatingWindowManager>
  );
}
