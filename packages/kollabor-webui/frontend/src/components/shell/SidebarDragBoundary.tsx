import { createContext, useContext, useLayoutEffect, useMemo, useRef, type ReactNode } from "react";
import { DragDropContext } from "@hello-pangea/dnd";
import type { SidebarDragHandlers, SidebarDragRouter } from "./sidebar-drag-router";

export const SidebarDragContext = createContext<SidebarDragRouter | null>(null);

/** Agent lists remain standalone locally and join the network's context inside a device. */
export function SidebarDragBoundary({ id, handlers, children }: {
  id: string;
  handlers: SidebarDragHandlers;
  children: ReactNode;
}) {
  const shared = useContext(SidebarDragContext);
  const latest = useRef(handlers);
  latest.current = handlers;
  const proxy = useMemo<SidebarDragHandlers>(() => ({
    owns: (key) => latest.current.owns(key),
    beforeCapture: (capture) => latest.current.beforeCapture(capture),
    end: (result, provided) => latest.current.end(result, provided),
  }), []);
  useLayoutEffect(() => shared?.register(id, proxy), [shared, id, proxy]);
  if (shared) return children;
  return (
    <DragDropContext
      dragHandleUsageInstructions="To reorder, press Space, use the arrow keys, and press Space to drop. Escape cancels. Drag outside the sidebar to hide, or use Hide Agent in the actions menu. Click or press Enter to open."
      onBeforeCapture={proxy.beforeCapture}
      onDragEnd={proxy.end}
    >
      {children}
    </DragDropContext>
  );
}
