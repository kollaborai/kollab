import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { Files, Filter, PanelLeft, Search, Settings } from "lucide-react";
import { Button } from "@/components/ui/button";
import { openWorkspaceFile, readFileContent, fileNameOf, extensionOf } from "@/components/code/open-file";
import { claimEditorWorkspace } from "@/components/code/workspace-tabs";
import { setCodeSession } from "@/lib/code-fetch";
import { cn } from "@/lib/utils";
import { GitPanel } from "@/components/code/git/GitPanel";
import { openWorkspaceDiff } from "@/components/code/open-file";
import { EditorConfigPanel } from "@/components/code/editor-config";
import { isDirty, useEditorStore } from "@/components/code/editor-store";
import { FileTree } from "@/components/code/file-tree";
import { FloatingFileSidebar, useFileSidebarVisibility } from "@/components/code/floating-sidebar";
import { QuickOpen } from "@/components/code/quick-open";
import { SearchPanel } from "@/components/code/search-panel";
import { SplitContainer } from "@/components/code/split-container";

export interface CodeEditorViewProps {
  /** The session whose daemon serves the files and git: a session opened from another computer shows that computer's. */
  sessionId: string;
  /** The session's device ("" for this computer). Tabs opened on another device are dropped like tabs from another folder. */
  device?: string;
  workspace: string;
  /** "code" shows files, search and settings in the sidebar; "git" shows the git panel there. Same editor panes either way. */
  mode?: "code" | "git";
}

/** Opens a file in the active pane (git panel's "open file"). */
async function openFileInActivePane(workspace: string, absPath: string): Promise<void> {
  await openWorkspaceFile(workspace, absPath, { pin: true });
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

/** The Code Editor view for one chat folder, docked in the chat column: file sidebar, split editor panes, quick open. */
export function CodeEditorView({ sessionId, device = "", workspace, mode = "code" }: CodeEditorViewProps) {
  // Set while rendering, so it is in place before any child effect fetches (see lib/code-fetch.ts).
  setCodeSession(sessionId);
  const sidebarView = useEditorStore((s) => s.sidebarView);
  const setSidebarView = useEditorStore((s) => s.setSidebarView);
  const [quickOpenOpen, setQuickOpenOpen] = useState(false);
  const [fileFilterOpen, setFileFilterOpen] = useState(false);
  const sidebar = useFileSidebarVisibility();
  const rootRef = useRef<HTMLDivElement>(null);

  // Git has its own view now: a sidebar choice saved as "git" falls back to the file tree
  useEffect(() => {
    if (sidebarView === "git") setSidebarView("files");
  }, [sidebarView, setSidebarView]);

  // a reload or a closed tab would lose unsaved edits: ask the browser to confirm while any buffer is dirty
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      const dirty = [...useEditorStore.getState().fileCache.values()].some((file) => isDirty(file));
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, []);

  // tabs opened in another folder are dropped, except those with unsaved edits; the folder reaches each sibling as the `workspace` prop
  useEffect(() => {
    claimEditorWorkspace(device ? `${device}:${workspace}` : workspace);
  }, [device, workspace]);

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
      // the view stays mounted while hidden behind Chat; its shortcuts only work while it shows
      if (!rootRef.current || rootRef.current.offsetParent === null) return;
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

  // Escape closes quick open
  const guardEscape = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Escape" || !quickOpenOpen) return;
    e.preventDefault();
    setQuickOpenOpen(false);
  };

  const folderLabel = workspace.split("/").filter(Boolean).slice(-2).join("/");

  return (
    <div ref={rootRef} className="flex min-h-0 flex-1 flex-col overflow-hidden" data-code-view="" onKeyDown={guardEscape}>
      <div className="flex shrink-0 items-center gap-1.5 px-3 py-1.5 text-xs font-medium">
        {sidebar.narrow && (
          <SidebarToggle label="Files sidebar" active={sidebar.visible} onClick={sidebar.toggle}>
            <PanelLeft className="h-4 w-4" />
          </SidebarToggle>
        )}
        <span className="min-w-0 truncate">
          {mode === "git" ? "Git" : "Code"} <span className="font-mono text-[10px] font-normal text-muted-foreground">{folderLabel}</span>
        </span>
      </div>
      <div className="relative flex min-h-0 flex-1 gap-2 overflow-hidden p-2">
        <FloatingFileSidebar visible={sidebar.visible} narrow={sidebar.narrow}>
          {mode === "git" ? (
            <GitPanel
              workspace={workspace}
              onOpenFile={(absPath) => void openFileInActivePane(workspace, absPath)}
              onOpenDiff={(absPath, headContent) => void openWorkspaceDiff(workspace, absPath, headContent)}
            />
          ) : (
            <>
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
              </div>

              {sidebarView === "files" && <FileTree workspace={workspace} filterOpen={fileFilterOpen} />}
              {sidebarView === "search" && <SearchPanel workspace={workspace} />}
              {sidebarView === "config" && <EditorConfigPanel />}
            </>
          )}
        </FloatingFileSidebar>

        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <SplitContainer workspace={workspace} />
        </div>
      </div>

      <QuickOpen open={quickOpenOpen} onClose={() => setQuickOpenOpen(false)} workspace={workspace} />
    </div>
  );
}
