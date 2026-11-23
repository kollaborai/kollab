// @vitest-environment jsdom
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mounts the real Code and Git panels against an engine that answers 502 daemon_outdated, as it
// does for an agent started before 0.22.0. Only the network seam (codeFetch) and the Monaco-heavy
// editor panes are replaced; everything that decides what the user reads is the real component.
const engine = vi.hoisted(() => ({
  calls: [] as string[],
  folders: "outdated" as "outdated" | "ok" | "broken",
}));

vi.mock("@/lib/code-fetch", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/code-fetch")>();
  const json = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  return {
    ...actual,
    codeFetch: async (url: string) => {
      engine.calls.push(url);
      if (url.startsWith("/code/folders") && engine.folders === "ok") {
        return json(200, { success: true, data: { default: "/w", folders: [{ path: "/w", branch: "main" }] } });
      }
      if (url.startsWith("/code/folders") && engine.folders === "broken") {
        return json(500, { success: false, error: { code: "boom", message: "boom" } });
      }
      return json(502, { success: false, error: { code: "daemon_outdated", message: actual.OUTDATED_NOTICE } });
    },
  };
});
vi.mock("@/components/code/split-container", () => ({ SplitContainer: () => null }));
vi.mock("@/components/code/quick-open", () => ({ QuickOpen: () => null, FileTypeIcon: () => null }));
vi.mock("@/components/code/floating-sidebar", () => ({
  FloatingFileSidebar: ({ children }: { children?: unknown }) => children,
  useFileSidebarVisibility: () => ({ visible: true, narrow: false, toggle: () => {}, hide: () => {} }),
  shouldHideSidebarAfterFileSelection: () => false,
}));

import { CodeEditorView } from "@/components/code/CodeEditorView";
import { FileTree } from "@/components/code/file-tree";
import { GitPanel } from "@/components/code/git/git-panel";
import { agentVersionNotice } from "@/components/shell/AgentVersionNotice";

const agent = { name: "lapis", agent_id: "a1", state: "idle", current_task: "", solo: false, project: "/w", version: "" };
const daemonNotice = {
  notice: agentVersionNotice({ engineVersion: "0.22.0+aaaa1111", agent, restartAgents: false }),
  fallbackName: "lapis",
  busy: false,
  onRestart: () => {},
};
const stopAndStart = "This agent started before Restart Agent existed. Run kollab --hub stop lapis in a terminal, then select its Offline row in the sidebar to start it again.";
const oldSentence = "Could not load folders from this agent. Restart it and try again.";

let host: HTMLDivElement;
let root: Root;

async function mount(element: ReactElement): Promise<string> {
  await act(async () => {
    root.render(element);
  });
  // let the mocked fetches and their state updates settle
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  return host.textContent ?? "";
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  engine.calls = [];
  engine.folders = "outdated";
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("Code and Git panels against a daemon_outdated engine", () => {
  it.each(["code", "git"] as const)("%s view: the folder list itself fails, and the stop-and-start notice shows", async (mode) => {
    const text = await mount(
      <CodeEditorView sessionId="s1" workspace="/w" mode={mode} daemonNotice={daemonNotice} />,
    );
    expect(engine.calls[0]).toBe("/code/folders");
    expect(text).toContain(stopAndStart);
    expect(text).not.toContain(oldSentence);
    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(text).not.toContain("Retry");
  });

  it("retries the folder list after the agent reaches the engine version", async () => {
    const previous = await mount(
      <CodeEditorView
        sessionId="s1"
        workspace="/w"
        mode="code"
        agentIsCurrent={false}
        daemonNotice={daemonNotice}
      />,
    );
    expect(previous).toContain(stopAndStart);

    engine.folders = "ok";
    const current = await mount(
      <CodeEditorView
        sessionId="s1"
        workspace="/w"
        mode="code"
        agentIsCurrent
        daemonNotice={{ ...daemonNotice, notice: null }}
      />,
    );
    expect(engine.calls.filter((url) => url.startsWith("/code/folders"))).toHaveLength(2);
    expect(current).not.toContain(stopAndStart);
  });

  it("keeps the generic message for a folder-list failure that is not daemon_outdated", async () => {
    engine.folders = "broken";
    const text = await mount(<CodeEditorView sessionId="s1" workspace="/w" mode="code" daemonNotice={daemonNotice} />);
    expect(text).toContain(oldSentence);
    expect(text).not.toContain(stopAndStart);
  });

  it("Code: the file tree shows the notice when only the tree call is outdated", async () => {
    engine.folders = "ok";
    const text = await mount(<CodeEditorView sessionId="s1" workspace="/w" mode="code" daemonNotice={daemonNotice} />);
    expect(text).toContain(stopAndStart);
    expect(text).not.toContain("Failed to load");
    expect(text).not.toContain("Retry");
  });

  it("Code: the real FileTree shows the notice", async () => {
    const text = await mount(<FileTree sessionId="s1" workspace="/w" daemonNotice={daemonNotice} />);
    expect(text).toContain(stopAndStart);
    expect(text).not.toContain("Retry");
  });

  it("Git: the real GitPanel shows the notice", async () => {
    const text = await mount(
      <GitPanel sessionId="s1" workspace="/w" daemonNotice={daemonNotice} onOpenFile={() => {}} onOpenDiff={() => {}} />,
    );
    expect(text).toContain(stopAndStart);
    expect(text).not.toContain("Retry");
  });
});
