import { createRoot } from "react-dom/client";
import { StashSelector } from "../../src/components/code/git/stash-selector";
import "../../src/styles.css";

createRoot(document.getElementById("root")!).render(
  <div style={{ width: 700, height: 500 }}><StashSelector sessionId="fixture" workspace="/fixture" /></div>,
);
