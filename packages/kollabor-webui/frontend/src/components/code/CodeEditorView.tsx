import { editorShortcutEnabled } from "@/components/code/editor-shortcuts";
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { Files, Filter, PanelLeft, Search, Settings } from "lucide-react";
import { Button } from "@/components/ui/button";
import { openWorkspaceFile, readFileContent, rememberFileRead, fileNameOf, extensionOf } from "@/components/code/open-file";
import { FolderBrowser } from "@/components/code/folder-browser";
import { claimEditorWorkspace, invalidateEditorSession, setEditorOwner, setEditorSession } from "@/components/code/workspace-tabs";
import { codeFetch, setCodeRoot, setCodeSession, unwrapCodeData } from "@/lib/code-fetch";
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
export function CodeEditorView(props: CodeEditorViewProps) {
  const [folders, setFolders] = useState<{ path: string; branch: string }[] | null>(null);
  const [loadedContext, setLoadedContext] = useState("");
  const [selected, setSelected] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const [error, setError] = useState("");
  const requestContext = JSON.stringify([props.device ?? "", props.sessionId]);
  useLayoutEffect(() => {
    setCodeSession(props.sessionId);
    setEditorSession(requestContext);
    return () => invalidateEditorSession(requestContext);
  }, [props.sessionId, requestContext]);
  useEffect(() => {
    let cancelled = false;
    setFolders(null);
    setLoadedContext("");
    setBrowsing(false);
    setError("");
    void codeFetch("/code/folders", {}, { sessionId: props.sessionId, root: "" }).then(async (response) => {
      if (!response.ok) throw new Error("Could not load folders from this agent.");
      const data = unwrapCodeData<{ folders: { path: string; branch: string }[]; default: string }>(await response.json());
      if (!cancelled) { setFolders(data.folders); setSelected(data.default); setLoadedContext(requestContext); }
    }).catch(() => { if (!cancelled) setError("Could not load folders from this agent. Restart it and try again."); });
    return () => { cancelled = true; };
  }, [props.sessionId, requestContext]);
  if (loadedContext && loadedContext !== requestContext) return <div className="p-3 text-sm text-muted-foreground">Loading folders…</div>;
  if (error) return <div role="alert" className="p-3 text-sm text-destructive">{error}</div>;
  if (!folders || !selected) return <div className="p-3 text-sm text-muted-foreground">Loading folders…</div>;
  const choices = folders.some((folder) => folder.path === selected) ? folders : [...folders, { path: selected, branch: "" }];
  return <>
    <CodeEditorFolder key={`${props.sessionId}:${props.device ?? ""}:${selected}`} {...props} workspace={selected} folders={choices} onFolderChange={setSelected} onBrowse={() => setBrowsing(true)} />
    <FolderBrowser open={browsing} initialPath={selected} onClose={() => setBrowsing(false)} onSelect={(path) => { setSelected(path); setBrowsing(false); }} />
  </>;
}

function CodeEditorFolder({ sessionId, device = "", workspace, mode = "code", folders, onFolderChange, onBrowse }: CodeEditorViewProps & {
  folders: { path: string; branch: string }[]; onFolderChange: (path: string) => void; onBrowse: () => void;
}) {
  // Commit the context before passive effects in child views issue requests.
  useLayoutEffect(() => {
    setCodeSession(sessionId);
    setCodeRoot(workspace);
    setEditorOwner(device ? `${device}:${workspace}` : workspace, JSON.stringify([device, sessionId]));
  }, [sessionId, device, workspace]);
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
        void readFileContent(workspace, path).then((read) => {
          if (cancelled) return;
          const store = useEditorStore.getState();
          if (store.fileCache.has(path)) return;
          if (read === null) {
            store.closeFile(pane.id, path);
            return;
          }
          const name = fileNameOf(path);
          const active = store.panes.find((p) => p.id === pane.id)?.activePath ?? null;
          rememberFileRead(path, read);
          store.openFile(pane.id, path, name, extensionOf(name), read.content);
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
      if (!editorShortcutEnabled()) return;
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


  return (
    <div ref={rootRef} className="flex min-h-0 flex-1 flex-col overflow-hidden" data-code-view="" onKeyDown={guardEscape}>
      <div className="flex shrink-0 items-center gap-1.5 px-3 py-1.5 text-xs font-medium">
        {sidebar.narrow && (
          <SidebarToggle label="Files sidebar" active={sidebar.visible} onClick={sidebar.toggle}>
            <PanelLeft className="h-4 w-4" />
          </SidebarToggle>
        )}
        <span>{mode === "git" ? "Git" : "Code"}</span>
        <select aria-label="Code and Git folder" value={workspace} onChange={(event) => {
          if (event.target.value === "__browse__") onBrowse(); else onFolderChange(event.target.value);
        }} className="min-w-0 max-w-full bg-transparent font-mono text-xs text-muted-foreground">
          {folders.map((folder) => <option key={folder.path} value={folder.path}>{folder.path}{folder.branch ? ` (${folder.branch})` : ""}</option>)}
          <option value="__browse__">Browse folders…</option>
        </select>
      </div>
      <div className="relative flex min-h-0 flex-1 gap-2 overflow-hidden p-2">
        <FloatingFileSidebar visible={sidebar.visible} narrow={sidebar.narrow}>
          {mode === "git" ? (
            <GitPanel
              sessionId={sessionId}
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
          <SplitContainer workspace={workspace} showFilePicker={sidebar.narrow && !sidebar.visible} onOpenFiles={sidebar.toggle} />
        </div>
      </div>

      <QuickOpen open={quickOpenOpen} onClose={() => setQuickOpenOpen(false)} workspace={workspace} />
    </div>
  );
}
