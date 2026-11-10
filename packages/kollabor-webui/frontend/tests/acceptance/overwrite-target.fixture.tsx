import { createRoot } from "react-dom/client";
import { EditorPane } from "../../src/components/code/editor-pane";
import { TabBar } from "../../src/components/code/tab-bar";
import { useEditorStore } from "../../src/components/code/editor-store";
import * as fileSave from "../../src/components/code/file-save";
import { setEditorOwner } from "../../src/components/code/workspace-tabs";
import { setCodeRoot, setCodeSession } from "../../src/lib/code-fetch";
import "../../src/styles.css";

const pane = useEditorStore.getState().activePaneId;
setCodeSession("fixture");
setCodeRoot("/fixture");
setEditorOwner("fixture:/fixture", "fixture", "fixture-device");
for (const name of ["a", "b"]) {
  const path = `/fixture/${name}.txt`;
  useEditorStore.getState().openFile(pane, path, `${name}.txt`, ".txt", "baseline");
  const remember = (fileSave as { rememberFileIdentities?: (path: string, file: string, root: string) => void })
    .rememberFileIdentities;
  remember?.(path, "a".repeat(64), "b".repeat(64));
  useEditorStore.getState().pinFile(path);
  useEditorStore.getState().updateContent(path, `${name} draft`);
}
useEditorStore.getState().setActiveFile(pane, "/fixture/a.txt");
let root = createRoot(document.getElementById("root")!);
export function mountPane() {
  root = createRoot(document.getElementById("root")!);
  root.render(<div data-code-view style={{ width: "100vw", height: 700 }}><TabBar paneId={pane} rootPath="/fixture" /><EditorPane paneId={pane} rootPath="/fixture" /></div>);
}
export function unmountPane() { root.unmount(); }
root.render(<div data-code-view style={{ width: "100vw", height: 700 }}><TabBar paneId={pane} rootPath="/fixture" /><EditorPane paneId={pane} rootPath="/fixture" /></div>);
