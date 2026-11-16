import { expect, test } from "vitest";
import { useEditorStore } from "../src/components/code/editor-store";
import { bufferBelongsHere, claimEditorWorkspace, editorOwnerEpoch, setEditorOwner } from "../src/components/code/workspace-tabs";

test("owner epoch changes on an away-and-back transition", () => {
  setEditorOwner("device-a:/work");
  const first = editorOwnerEpoch();
  setEditorOwner("device-a:/work");
  expect(editorOwnerEpoch()).toBe(first);
  setEditorOwner("device-b:/work");
  setEditorOwner("device-a:/work");
  expect(editorOwnerEpoch()).toBeGreaterThan(first);
});

test("owner epoch changes when sessions change on the same device and folder", () => {
  setEditorOwner("device-a:/work", "session-a");
  const first = editorOwnerEpoch();
  setEditorOwner("device-a:/work", "session-b");
  setEditorOwner("device-a:/work", "session-a");
  expect(editorOwnerEpoch()).toBeGreaterThan(first);
});

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
