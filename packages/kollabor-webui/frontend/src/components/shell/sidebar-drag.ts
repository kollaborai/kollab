import type { DropResult } from "@hello-pangea/dnd";

type DragBox = { left: number; top: number; width: number; height: number };

/** Fixed rows scale around their center, so translate that center onto the footer button. */
export function hiddenAgentTransform(row: DragBox, target: DragBox): string {
  const x = target.left + target.width / 2 - row.left - row.width / 2;
  const y = target.top + target.height / 2 - row.top - row.height / 2;
  return `translate(${x}px, ${y}px) scale(0.02)`;
}

/** Leave a gutter so a small sideways slip still drops back into the sidebar. */
export function outsideSidebar(clientX: number, bounds: { left: number; right: number }): boolean {
  return Number.isFinite(clientX) && clientX >= 0 && bounds.right > bounds.left &&
    (clientX < bounds.left - 32 || clientX > bounds.right + 32);
}

/** A canceled/keyboard drag never hides a row, and sorting never crosses scopes. */
export function sidebarDropAction(
  { reason, mode, source, destination }: Pick<DropResult, "reason" | "mode" | "source" | "destination">,
  outside: boolean,
): { type: "hide" } | { type: "reorder"; index: number } | null {
  if (reason !== "DROP") return null;
  if (mode === "FLUID" && outside) return { type: "hide" };
  if (destination?.droppableId !== source.droppableId || destination.index === source.index) return null;
  return { type: "reorder", index: destination.index };
}
