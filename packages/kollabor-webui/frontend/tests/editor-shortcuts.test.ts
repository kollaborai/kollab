import { afterEach, expect, test, vi } from "vitest";
import { useEditorStore } from "../src/components/code/editor-store";
import { editorShortcutEnabled } from "../src/components/code/editor-shortcuts";

afterEach(() => vi.unstubAllGlobals());
test("shortcuts require a visible editor and the active pane", () => {
  let visible = true;
  let modal = false;
  vi.stubGlobal("document", {
    querySelector: (selector: string) => selector === "[data-code-view]" ? { getClientRects: () => ({ length: visible ? 1 : 0 }) } : null,
    querySelectorAll: () => modal ? [{ getClientRects: () => ({ length: 1 }) }] : [],
  });
  useEditorStore.setState({ activePaneId: "pane-a" });
  expect(editorShortcutEnabled("pane-a")).toBe(true);
  expect(editorShortcutEnabled("pane-b")).toBe(false);
  modal = true;
  expect(editorShortcutEnabled("pane-a")).toBe(false);
  modal = false;
  visible = false;
  expect(editorShortcutEnabled("pane-a")).toBe(false);
  expect(editorShortcutEnabled()).toBe(false);
});
