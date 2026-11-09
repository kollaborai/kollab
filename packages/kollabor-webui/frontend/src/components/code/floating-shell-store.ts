import { create } from "zustand";

/**
 * Open state for the floating code editor and floating terminal. The folder both work in is the
 * chat's folder (`Session.workspace`), set by App when the active session changes.
 * Panels are mounted once at the root (App.tsx) and render nothing while closed.
 */
interface FloatingShellStore {
  /** The folder the editor, git panel and terminal work in; null until a session with a folder is active. */
  workspace: string | null;
  codeOpen: boolean;
  terminalOpen: boolean;
  setWorkspace: (workspace: string | null) => void;
  setCodeOpen: (open: boolean) => void;
  setTerminalOpen: (open: boolean) => void;
  toggleCode: () => void;
  toggleTerminal: () => void;
}

export const useFloatingShell = create<FloatingShellStore>((set) => ({
  workspace: null,
  codeOpen: false,
  terminalOpen: false,
  setWorkspace: (workspace) => set({ workspace }),
  setCodeOpen: (codeOpen) => set({ codeOpen }),
  setTerminalOpen: (terminalOpen) => set({ terminalOpen }),
  toggleCode: () => set((s) => ({ codeOpen: !s.codeOpen })),
  toggleTerminal: () => set((s) => ({ terminalOpen: !s.terminalOpen })),
}));
