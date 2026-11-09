import { lazy, Suspense, useEffect } from "react";
import { useFloatingShell } from "./floating-shell-store";

// Monaco and xterm are large: each panel loads on first open, not with the main bundle.
const FloatingCodePanel = lazy(() =>
  import("./FloatingCodePanel").then((m) => ({ default: m.FloatingCodePanel })),
);
const FloatingTerminalPanel = lazy(() =>
  import("../terminal/FloatingTerminalPanel").then((m) => ({ default: m.FloatingTerminalPanel })),
);

/**
 * Mounts the floating code editor and terminal once, at the app root (never inside a transformed
 * ancestor, which would trap their fixed positioning). `workspace` is the active chat's folder.
 * Ctrl+` toggles the terminal.
 */
export function FloatingShellHost({ workspace }: { workspace: string | null }) {
  const { codeOpen, terminalOpen, setWorkspace, setCodeOpen, setTerminalOpen, toggleTerminal } =
    useFloatingShell();

  useEffect(() => {
    setWorkspace(workspace);
    // No folder (no session) means nothing to edit; close rather than point at a stale folder.
    if (!workspace) {
      setCodeOpen(false);
      setTerminalOpen(false);
    }
  }, [workspace, setWorkspace, setCodeOpen, setTerminalOpen]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey && !event.metaKey && !event.altKey && event.key === "`") {
        event.preventDefault();
        if (workspace) toggleTerminal();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [workspace, toggleTerminal]);

  if (!workspace) return null;
  return (
    <Suspense fallback={null}>
      {codeOpen ? <FloatingCodePanel workspace={workspace} onClose={() => setCodeOpen(false)} /> : null}
      {terminalOpen ? (
        <FloatingTerminalPanel workspace={workspace} onClose={() => setTerminalOpen(false)} />
      ) : null}
    </Suspense>
  );
}
