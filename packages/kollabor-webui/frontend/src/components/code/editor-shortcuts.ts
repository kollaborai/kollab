import { useEditorStore } from "@/components/code/editor-store";

/** Window-level shortcuts belong only to the visible editor and, when specified, its active pane. */
export function editorShortcutEnabled(paneId?: string): boolean {
  const view = document.querySelector("[data-code-view]");
  if (!view || view.getClientRects().length === 0) return false;
  if ([...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')].some((modal) => modal.getClientRects().length > 0)) return false;
  return paneId === undefined || useEditorStore.getState().activePaneId === paneId;
}
