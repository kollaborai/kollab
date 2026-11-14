import { editorOwner, editorOwnerEpoch } from "@/components/code/workspace-tabs";
import { useState, useCallback, type DragEvent } from "react";
import { ChevronRight, CircleX, Save } from "lucide-react";
import { saveWorkspaceFile } from "@/components/code/file-save";
import { useEditorStore, usePane, isDirty, setBufferSaveNotice } from "@/components/code/editor-store";
import { getFileAccentColor } from "@/components/code/file-tree";

interface TabBarProps {
  paneId: string;
  rootPath: string;
}

export function TabBar({ paneId, rootPath }: TabBarProps) {
  const { openFiles, activeFile, pane } = usePane(paneId);
  const setActiveFile = useEditorStore((s) => s.setActiveFile);
  const pinFile = useEditorStore((s) => s.pinFile);
  const closeFile = useEditorStore((s) => s.closeFile);
  const markSaved = useEditorStore((s) => s.markSaved);
  const reorderFiles = useEditorStore((s) => s.reorderFiles);

  const handleSave = useCallback(async (filePath: string, content: string) => {
    const owner = editorOwner();
    const epoch = editorOwnerEpoch();
    const initial = useEditorStore.getState().fileCache.get(filePath);
    if (!initial) return;
    const { bufferId } = initial;
    const revision = initial.saveRevision ?? 0;
    const result = await saveWorkspaceFile(rootPath, filePath, content);
    const current = useEditorStore.getState().fileCache.get(filePath);
    if (!current || current.bufferId !== bufferId) return;
    const sameContext = owner === editorOwner() && epoch === editorOwnerEpoch();
    if (result.ok && sameContext) {
      markSaved(filePath, content);
    } else {
      const message = result.ok ? "The save completed while the workspace changed. Check the file before retrying."
        : `${result.message}${result.stale ? " Save again to review the changed file." : ""}`;
      if (!result.ok && result.stale && (current.saveRevision ?? 0) !== revision) return;
      setBufferSaveNotice(filePath, bufferId, { owner, epoch, message, stale: false });
      if (sameContext) {
        setActiveFile(paneId, filePath);
      }
    }
  }, [markSaved, paneId, rootPath, setActiveFile]);

  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dropTarget, setDropTarget] = useState<number | null>(null);

  const handleDragStart = useCallback(
    (e: DragEvent<HTMLDivElement>, index: number) => {
      setDragIndex(index);
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", String(index));
    },
    []
  );

  const handleDragOver = useCallback(
    (e: DragEvent<HTMLDivElement>, index: number) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      if (dragIndex === null || dragIndex === index) {
        setDropTarget(null);
        return;
      }

      const rect = e.currentTarget.getBoundingClientRect();
      const midX = rect.left + rect.width / 2;
      const insertAt = e.clientX < midX ? index : index + 1;
      setDropTarget(insertAt);
    },
    [dragIndex]
  );

  const handleDrop = useCallback(
    (e: DragEvent<HTMLDivElement>, index: number) => {
      e.preventDefault();
      if (dragIndex === null) return;

      const rect = e.currentTarget.getBoundingClientRect();
      const midX = rect.left + rect.width / 2;
      let toIndex = e.clientX < midX ? index : index + 1;

      // adjust for removal shift
      if (dragIndex < toIndex) toIndex--;

      if (dragIndex !== toIndex) {
        reorderFiles(paneId, dragIndex, toIndex);
      }

      setDragIndex(null);
      setDropTarget(null);
    },
    [dragIndex, paneId, reorderFiles]
  );

  const handleDragEnd = useCallback(() => {
    setDragIndex(null);
    setDropTarget(null);
  }, []);

  if (!pane || openFiles.length === 0) return null;

  return (
    <div
      className="flex flex-1 min-w-0 items-center gap-0 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      onDragOver={(e) => e.preventDefault()}
    >
      {openFiles.map((file, index) => {
        const isActive = activeFile?.path === file.path;
        const dirty = isDirty(file);
        const accent = getFileAccentColor(file.path, rootPath);
        const isDragging = dragIndex === index;
        const showDropBefore = dropTarget === index;
        const showDropAfter =
          dropTarget === index + 1 && index === openFiles.length - 1;

        // breadcrumb: the path relative to the workspace
        const rel = file.path.startsWith(rootPath)
          ? file.path.slice(rootPath.length + 1)
          : file.path;
        const segments = rel.split("/");

        return (
          <div
            key={file.path}
            role="button"
            tabIndex={0}
            draggable
            onDragStart={(e) => handleDragStart(e, index)}
            onDragOver={(e) => handleDragOver(e, index)}
            onDrop={(e) => handleDrop(e, index)}
            onDragEnd={handleDragEnd}
            onClick={() => setActiveFile(paneId, file.path)}
            onDoubleClick={() => pinFile(file.path)}
            onKeyDown={(e) => {
              if (e.key === "Enter") setActiveFile(paneId, file.path);
            }}
            className={`relative flex items-center gap-1 px-2 py-0.5 text-[9px] shrink-0 cursor-pointer transition-all rounded-md group ${
              isDragging ? "opacity-40" : ""
            } ${
              isActive
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
            }`}
            style={
              isActive
                ? {
                    background: `${accent}0a`,
                    boxShadow: `0 0 0 1px ${accent}15`,
                  }
                : undefined
            }
          >
            {showDropBefore && (
              <div className="absolute left-0 top-1 bottom-1 w-0.5 bg-foreground/40 rounded-full" />
            )}
            {dirty && (
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500/70 shrink-0" />
            )}
            <span
              className={`flex items-center gap-0.5 font-mono ${!file.pinned ? "italic" : ""}`}
            >
              {segments.map((seg, si) => (
                <span key={si} className="flex items-center gap-0.5 shrink-0">
                  {si > 0 && (
                    <ChevronRight className="h-2 w-2 text-muted-foreground/50 shrink-0" />
                  )}
                  <span className={si === segments.length - 1 ? undefined : "opacity-60"}>
                    {seg}
                  </span>
                </span>
              ))}
            </span>
            {dirty && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  handleSave(file.path, file.content);
                }}
                className="shrink-0 opacity-0 group-hover:opacity-80 hover:opacity-100 transition-all text-amber-500/70 hover:text-amber-500"
                title="Save (Cmd+S)"
                aria-label="Save file"
              >
                <Save className="h-3 w-3" />
              </button>
            )}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                if (
                  dirty &&
                  !window.confirm(`Discard unsaved changes to ${file.name}?`)
                )
                  return;
                closeFile(paneId, file.path);
              }}
              className="shrink-0 opacity-0 group-hover:opacity-60 hover:opacity-100 transition-all"
              aria-label="Close tab"
            >
              <CircleX className="h-3 w-3" />
            </button>
            {showDropAfter && (
              <div className="absolute right-0 top-1 bottom-1 w-0.5 bg-foreground/40 rounded-full" />
            )}
          </div>
        );
      })}
    </div>
  );
}
