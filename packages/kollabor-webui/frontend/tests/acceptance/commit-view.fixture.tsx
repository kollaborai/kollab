import { createRoot } from "react-dom/client";
import { EditorPane } from "../../src/components/code/editor-pane";
import { GitPanel } from "../../src/components/code/git/git-panel";
import { useEditorStore } from "../../src/components/code/editor-store";
import "../../src/styles.css";

const pane = useEditorStore.getState().activePaneId;
useEditorStore.getState().openFile(pane, "/fixture/.diffs/abcd.diff", "abcd.diff", ".diff", "real file");
useEditorStore.getState().pinFile("/fixture/.diffs/abcd.diff");
createRoot(document.getElementById("root")!).render(
  <div style={{ display: "flex", width: 1100, height: 700 }}>
    <div style={{ width: 350 }}><GitPanel sessionId="fixture" workspace="/fixture" onOpenFile={() => {}} onOpenDiff={() => {}} /></div>
    <div style={{ flex: 1 }}><EditorPane paneId={pane} rootPath="/fixture" /></div>
  </div>,
);
