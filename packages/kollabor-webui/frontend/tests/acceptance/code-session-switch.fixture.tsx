import { useState } from "react";
import { createRoot } from "react-dom/client";
import { CodeEditorView } from "../../src/components/code/CodeEditorView";
import "../../src/styles.css";

function Fixture() {
  const [sessionId, setSessionId] = useState("session-a");
  const [show, setShow] = useState(true);
  return <><button onClick={() => setSessionId("session-b")}>Switch session</button>
    <button onClick={() => setSessionId("session-c")}>Switch to unavailable session</button>
    <button onClick={() => setShow(false)}>Leave editor</button>
    <div style={{ display: "flex", width: 1000, height: 700 }}>
      {show && <CodeEditorView sessionId={sessionId} device="device" workspace="/shared" mode="code" />}
    </div></>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
