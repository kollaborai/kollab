// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const engine = vi.hoisted(() => ({ calls: [] as string[] }));

vi.mock("@/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api")>();
  return {
    ...actual,
    EngineApi: class {
      async request(url: string) {
        engine.calls.push(url);
        if (url.endsWith("/code/folders")) {
          return new Response(JSON.stringify({
            success: true,
            data: { default: "/w", folders: [{ path: "/w", branch: "main" }] },
          }), { status: 200, headers: { "Content-Type": "application/json" } });
        }
        if (url.includes("/code/fs/tree")) {
          const body = { success: false, error: { code: "daemon_outdated", message: "This agent runs older code. Restart it to use Code, Git and Terminal." } };
          const error = new Error(body.error.message);
          Object.assign(error, { status: 502, body });
          throw error;
        }
        throw new Error(`Unexpected code request: ${url}`);
      }
    },
  };
});

vi.mock("@/components/code/editor-shortcuts", () => ({ editorShortcutEnabled: () => true }));
vi.mock("@/components/code/file-tree", () => ({ FileTree: () => null }));
vi.mock("@/components/code/git/GitPanel", () => ({ GitPanel: () => null }));
vi.mock("@/components/code/split-container", () => ({ SplitContainer: () => null }));
vi.mock("@/components/code/floating-sidebar", () => ({
  FloatingFileSidebar: ({ children }: { children?: unknown }) => children,
  useFileSidebarVisibility: () => ({ visible: true, narrow: false, toggle: () => {}, hide: () => {} }),
}));

import { CodeEditorView } from "@/components/code/CodeEditorView";
import { agentVersionNotice } from "@/components/shell/AgentVersionNotice";

const agent = { name: "lapis", agent_id: "a1", state: "idle", current_task: "", solo: false, project: "/w", version: "" };
const notice = agentVersionNotice({ engineVersion: "0.22.0+aaaa1111", agent, restartAgents: false });
const daemonNotice = { notice, fallbackName: "lapis", busy: false, onRestart: () => {} };
const stopAndStart = "This agent started before Restart Agent existed. Run kollab --hub stop lapis in a terminal, then select its Offline row in the sidebar to start it again.";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  engine.calls = [];
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("daemon_outdated from a Code descendant", () => {
  it("replaces Quick Open's raw error with the shared session recovery notice", async () => {
    await act(async () => {
      root.render(<CodeEditorView sessionId="s1" workspace="/w" mode="code" daemonNotice={daemonNotice} />);
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "p", ctrlKey: true, bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(engine.calls).toContain("/sessions/s1/code/folders");
    expect(engine.calls.some((url) => url.includes("/sessions/s1/code/fs/tree"))).toBe(true);
    expect(host.textContent).toContain(stopAndStart);
    expect(host.textContent).not.toContain("This agent runs older code. Restart it to use Code, Git and Terminal.");
    expect(host.textContent).not.toContain("Retry");
  });
});
