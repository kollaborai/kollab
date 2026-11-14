import { editorShortcutEnabled } from "@/components/code/editor-shortcuts";
import { useRef, useCallback, useState, useEffect } from "react";
import { useEditorStore, type SplitNode } from "@/components/code/editor-store";
import { TabBar } from "./tab-bar";
import { EditorPane } from "./editor-pane";

interface SplitContainerProps {
  workspace: string;
  showFilePicker?: boolean;
  onOpenFiles?: () => void;
}

export function SplitContainer({ workspace, showFilePicker = false, onOpenFiles = () => {} }: SplitContainerProps) {
  const splitTree = useEditorStore((s) => s.splitTree);
  const activePaneId = useEditorStore((s) => s.activePaneId);
  const setActivePane = useEditorStore((s) => s.setActivePane);
  const splitRight = useEditorStore((s) => s.splitRight);
  const splitDown = useEditorStore((s) => s.splitDown);

  // keyboard shortcuts: cmd+\ split right, cmd+shift+\ split down
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!editorShortcutEnabled()) return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key === "\\") {
        e.preventDefault();
        if (e.shiftKey) {
          splitDown(activePaneId);
        } else {
          splitRight(activePaneId);
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [activePaneId, splitRight, splitDown]);

  // click anywhere in container sets active pane
  const handleContainerClick = useCallback(
    (e: React.SyntheticEvent) => {
      const target = e.target as HTMLElement;
      const paneLeaf = target.closest("[data-pane-id]");
      if (paneLeaf) {
        const paneId = paneLeaf.getAttribute("data-pane-id");
        if (paneId) setActivePane(paneId);
      }
    },
    [setActivePane]
  );

  return (
    <div
      className="flex-1 flex flex-col overflow-hidden"
      onClick={handleContainerClick}
      onFocus={handleContainerClick}
    >
      <SplitRender node={splitTree} workspace={workspace} isActivePaneId={activePaneId} showFilePicker={showFilePicker} onOpenFiles={onOpenFiles} />
    </div>
  );
}

interface SplitRenderProps {
  node: SplitNode;
  workspace: string;
  isActivePaneId: string;
  showFilePicker: boolean;
  onOpenFiles: () => void;
}

function SplitRender({ node, workspace, isActivePaneId, showFilePicker, onOpenFiles }: SplitRenderProps) {
  if (node.type === "leaf") {
    const isActive = node.paneId === isActivePaneId;
    return (
      <PaneLeaf paneId={node.paneId} workspace={workspace} isActive={isActive} showFilePicker={showFilePicker} onOpenFiles={onOpenFiles} />
    );
  }

  return (
    <SplitBranch
      direction={node.direction}
      first={node.first}
      second={node.second}
      sizePercent={node.sizePercent}
      workspace={workspace}
      isActivePaneId={isActivePaneId}
      showFilePicker={showFilePicker}
      onOpenFiles={onOpenFiles}
    />
  );
}

interface SplitBranchProps {
  direction: "horizontal" | "vertical";
  first: SplitNode;
  second: SplitNode;
  sizePercent: number;
  workspace: string;
  isActivePaneId: string;
  showFilePicker: boolean;
  onOpenFiles: () => void;
}

function SplitBranch({
  direction,
  first,
  second,
  sizePercent,
  workspace,
  isActivePaneId,
  showFilePicker,
  onOpenFiles,
}: SplitBranchProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [localSize, setLocalSize] = useState(sizePercent);

  const handleDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();

      const container = containerRef.current;
      if (!container) return;

      const rect = container.getBoundingClientRect();
      const startPos = direction === "horizontal" ? e.clientX : e.clientY;
      const startSize = localSize;

      const handleMouseMove = (ev: MouseEvent) => {
        const currentPos = direction === "horizontal" ? ev.clientX : ev.clientY;
        const delta = currentPos - startPos;
        const containerSize = direction === "horizontal" ? rect.width : rect.height;
        const deltaPercent = (delta / containerSize) * 100;
        const newSize = Math.min(70, Math.max(30, startSize + deltaPercent));
        setLocalSize(newSize);
      };

      const handleMouseUp = () => {
        document.removeEventListener("mousemove", handleMouseMove);
        document.removeEventListener("mouseup", handleMouseUp);
      };

      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
    },
    [direction, localSize]
  );

  const isHorizontal = direction === "horizontal";

  return (
    <div
      ref={containerRef}
      className={`flex ${isHorizontal ? "flex-row" : "flex-col"} flex-1 h-full overflow-hidden`}
    >
      <div style={{ flex: `0 0 calc(${localSize}% - 2px)` }} className="overflow-hidden h-full">
        <SplitRender node={first} workspace={workspace} isActivePaneId={isActivePaneId} showFilePicker={showFilePicker} onOpenFiles={onOpenFiles} />
      </div>

      <div
        onMouseDown={handleDragStart}
        className={`shrink-0 ${
          isHorizontal
            ? "w-1 cursor-col-resize hover:bg-foreground/10 active:bg-foreground/15"
            : "h-1 cursor-row-resize hover:bg-foreground/10 active:bg-foreground/15"
        } transition-colors`}
      />

      <div style={{ flex: `0 0 calc(${100 - localSize}% - 2px)` }} className="overflow-hidden h-full">
        <SplitRender node={second} workspace={workspace} isActivePaneId={isActivePaneId} showFilePicker={showFilePicker} onOpenFiles={onOpenFiles} />
      </div>
    </div>
  );
}

interface PaneLeafProps {
  paneId: string;
  workspace: string;
  isActive: boolean;
  showFilePicker: boolean;
  onOpenFiles: () => void;
}

function PaneLeaf({ paneId, workspace, isActive, showFilePicker, onOpenFiles }: PaneLeafProps) {
  return (
    <div
      data-pane-id={paneId}
      className={`flex flex-col h-full overflow-hidden ${isActive ? "opacity-100" : "opacity-80"}`}
    >
      <div className="shrink-0">
        <TabBar paneId={paneId} rootPath={workspace} />
      </div>
      <EditorPane paneId={paneId} rootPath={workspace} showFilePicker={showFilePicker} onOpenFiles={onOpenFiles} />
    </div>
  );
}
