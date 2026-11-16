import { useEffect, useState } from "react";

import { QuickOpen } from "@/components/code/quick-open";
import { SplitContainer } from "@/components/code/split-container";
// Local Monaco bundle, its workers and the kollab-dark theme. Imported here so they are configured
// before any editor under this panel mounts.
import "@/components/code/monaco-setup";

/**
 * Monaco editor surface for one workspace folder: the tabbed, split editor panes and the cmd+p quick
 * open that opens files into them. The sidebar, git panel and search are composed by CodeEditorView.
 */
export function CodeEditorClient({ workspace }: { workspace: string }) {
  const [quickOpenVisible, setQuickOpenVisible] = useState(false);

  // cmd+p toggles quick open
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "p") {
        e.preventDefault();
        setQuickOpenVisible((v) => !v);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <QuickOpen
        open={quickOpenVisible}
        onClose={() => setQuickOpenVisible(false)}
        workspace={workspace}
      />
      <SplitContainer workspace={workspace} />
    </div>
  );
}
