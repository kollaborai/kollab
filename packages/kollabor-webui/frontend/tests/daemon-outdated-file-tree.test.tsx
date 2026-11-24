// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const engine = vi.hoisted(() => ({ treeCalls: 0, rejectFirstTree: null as ((error: unknown) => void) | null }));

vi.mock("@/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api")>();
  return {
    ...actual,
    EngineApi: class {
      async request(url: string) {
        if (url.includes("/code/fs/tree")) {
          engine.treeCalls += 1;
          if (engine.treeCalls === 1) {
            return new Promise((_resolve, reject) => { engine.rejectFirstTree = reject; });
          }
          return new Response(JSON.stringify({ success: true, data: { tree: [] } }), { status: 200 });
        }
        if (url.includes("/code/fs/file")) {
          return new Response(JSON.stringify({ success: true, data: {} }), { status: 200 });
        }
        throw new Error("Unexpected code request");
      }
    },
  };
});

import { FileTree } from "@/components/code/file-tree";
import { codeFetch, OUTDATED_NOTICE, setCodeSession } from "@/lib/code-fetch";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  engine.treeCalls = 0;
  engine.rejectFirstTree = null;
  setCodeSession("s1", true);
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("FileTree stale daemon_outdated response", () => {
  it("keeps the current tree after another same-session code request succeeds", async () => {
    await act(async () => {
      root.render(<FileTree sessionId="s1" workspace="/w" daemonNotice={{ notice: null, busy: false, onRestart: () => {} }} />);
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    const currentResponse = await codeFetch("/api/fs/file?path=/w/current.txt", {}, { sessionId: "s1", root: "/w" });
    expect(currentResponse.ok).toBe(true);

    await act(async () => {
      const error = new Error(OUTDATED_NOTICE);
      Object.assign(error, {
        status: 502,
        body: { success: false, error: { code: "daemon_outdated", message: OUTDATED_NOTICE } },
      });
      engine.rejectFirstTree?.(error);
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(host.textContent).not.toContain(OUTDATED_NOTICE);
  });
});
