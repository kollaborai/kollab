import type { BeforeCapture, DropResult, ResponderProvided } from "@hello-pangea/dnd";

export type SidebarDragHandlers = {
  owns: (draggableId: string) => boolean;
  beforeCapture: (capture: BeforeCapture) => void;
  end: (result: DropResult, provided: ResponderProvided) => void;
};

/** Nested lists share one DnD context; only the list that lifted a row handles its drop. */
export function createSidebarDragRouter() {
  const lists = new Map<string, SidebarDragHandlers>();
  let active: { id: string; draggableId: string; handlers: SidebarDragHandlers } | null = null;
  return {
    register(id: string, handlers: SidebarDragHandlers) {
      lists.set(id, handlers);
      // A cloned device can replace its child lists before the old effects clean up.
      return () => { if (lists.get(id) === handlers) lists.delete(id); };
    },
    beforeCapture(capture: BeforeCapture): boolean {
      active = null;
      for (const [id, handlers] of lists) {
        if (!handlers.owns(capture.draggableId)) continue;
        active = { id, draggableId: capture.draggableId, handlers };
        handlers.beforeCapture(capture);
        return true;
      }
      return false;
    },
    end(result: DropResult, provided: ResponderProvided) {
      const origin = active;
      active = null;
      if (!origin) return;
      const valid = result.draggableId === origin.draggableId &&
        result.source.droppableId === origin.id && result.type === origin.id &&
        (!result.destination || result.destination.droppableId === origin.id);
      // Still notify the originating list on invalid completion so its polling hold releases.
      origin.handlers.end(valid ? result : {
        ...result,
        draggableId: origin.draggableId,
        source: { ...result.source, droppableId: origin.id },
        reason: "CANCEL",
        destination: null,
      }, provided);
    },
  };
}

export type SidebarDragRouter = ReturnType<typeof createSidebarDragRouter>;
