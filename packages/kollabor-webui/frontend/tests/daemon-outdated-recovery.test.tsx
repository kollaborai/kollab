// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const engine = vi.hoisted(() => ({
  calls: [] as string[],
  deferTree: false,
  succeedTree: false,
  rejectTree: null as ((error: unknown) => void) | null,
}));

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
          if (engine.deferTree) {
            return new Promise((_resolve, reject) => { engine.rejectTree = reject; });
          }
          if (engine.succeedTree) {
            return new Response(JSON.stringify({ success: true, data: { entries: [] } }), { status: 200, headers: { "Content-Type": "application/json" } });
          }
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
import { codeFetch } from "@/lib/code-fetch";

const agent = { name: "lapis", agent_id: "a1", state: "idle", current_task: "", solo: false, project: "/w", version: "" };
const notice = agentVersionNotice({ engineVersion: "0.22.0+aaaa1111", agent, restartAgents: false });
const daemonNotice = { notice, fallbackName: "lapis", busy: false, onRestart: () => {} };
const stopAndStart = "This agent started before Restart Agent existed. Run kollab --hub stop lapis in a terminal, then select its Offline row in the sidebar to start it again.";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  engine.calls = [];
  engine.deferTree = false;
  engine.succeedTree = false;
  engine.rejectTree = null;
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

  it("ignores a late daemon_outdated response from before the agent reaches the engine version", async () => {
    await act(async () => {
      root.render(<CodeEditorView sessionId="s1" workspace="/w" mode="code" daemonNotice={daemonNotice} />);
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    engine.deferTree = true;
    const staleRequest = codeFetch("/code/fs/tree", {}, { sessionId: "s1", root: "/w" });
    expect(engine.calls.some((url) => url.includes("/sessions/s1/code/fs/tree"))).toBe(true);

    await act(async () => {
      root.render(
        <CodeEditorView
          sessionId="s1"
          workspace="/w"
          mode="code"
          agentIsCurrent
          daemonNotice={{ ...daemonNotice, notice: null }}
        />,
      );
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    const error = new Error("This agent runs older code. Restart it to use Code, Git and Terminal.");
    Object.assign(error, {
      status: 502,
      body: {
        success: false,
        error: { code: "daemon_outdated", message: error.message },
      },
    });
    engine.rejectTree?.(error);
    await act(async () => { await staleRequest; });

    expect(host.textContent).not.toContain("This agent runs older code. Restart it to use Code, Git and Terminal.");
  });

  it("ignores a late daemon_outdated response after a newer same-session request succeeds", async () => {
    await act(async () => {
      root.render(<CodeEditorView sessionId="s1" workspace="/w" mode="code" daemonNotice={daemonNotice} />);
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    engine.deferTree = true;
    const staleRequest = codeFetch("/code/fs/tree", {}, { sessionId: "s1", root: "/w" });

    engine.deferTree = false;
    engine.succeedTree = true;
    const successfulRequest = codeFetch("/code/fs/tree", {}, { sessionId: "s1", root: "/w" });
    await successfulRequest;

    const error = new Error("This agent runs older code. Restart it to use Code, Git and Terminal.");
    Object.assign(error, {
      status: 502,
      body: {
        success: false,
        error: { code: "daemon_outdated", message: error.message },
      },
    });
    engine.rejectTree?.(error);
    await act(async () => { await staleRequest; });

    expect(host.textContent).not.toContain(stopAndStart);
  });
});
