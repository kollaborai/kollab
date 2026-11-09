import { expect, test } from "vitest";
import { useEditorStore } from "../src/components/code/editor-store";
import { bufferBelongsHere, claimEditorWorkspace, setEditorOwner } from "../src/components/code/workspace-tabs";

test("dirty buffers retain their original device owner even without local storage", () => {
  const path = "/shared/a.txt";
  setEditorOwner("device-a:/shared");
  claimEditorWorkspace("device-a:/shared");
  const store = useEditorStore.getState();
  store.openFile(store.activePaneId, path, "a.txt", ".txt", "original");
  useEditorStore.getState().updateContent(path, "unsaved");
  setEditorOwner("device-b:/shared");
  claimEditorWorkspace("device-b:/shared");
  expect(useEditorStore.getState().fileCache.get(path)?.content).toBe("unsaved");
  expect(bufferBelongsHere(path)).toBe(false);
  setEditorOwner("device-a:/shared");
  claimEditorWorkspace("device-a:/shared");
  expect(bufferBelongsHere(path)).toBe(true);
});
