// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EngineApi } from "../src/api.ts";
import { ChatStoreProvider, type ChatSnapshot, type LiveStore } from "../src/chat-store.ts";
import type { ChatRow } from "../src/chat-stream.ts";
import {
  PermissionCancellationProvider,
  usePendingPermissionCancellation,
} from "../src/permission-cancellation.ts";
import {
  markPermissionPromptPreviewVisible,
  registerRecoveredPermissionPrompt,
} from "../src/permission-preview-state.ts";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const promptArgs = {
  tool_id: "shell-1",
  request_id: "parent-1",
  permission_id: "permission-1",
  details: { source: "acp", parked: false },
};
const promptRow: ChatRow = {
  id: "row-1", seq: 1, kind: "tool", role: "assistant", turn_id: "turn-1", status: "complete",
  parts: [{ type: "tool-call", tool_call_id: "call-1", tool_name: "request_permission", args: promptArgs }],
  created_at: "2026-11-25T00:00:00Z",
};

function fakeStore(rows: ChatRow[] = [promptRow]): LiveStore {
  const snapshot: ChatSnapshot = {
    rows, inputs: {}, goalReplies: {}, running: false, connection: "live", hasEarlier: false, olderStatus: "idle",
  };
  return {
    rows: snapshot.rows,
    inputs: snapshot.inputs,
    running: snapshot.running,
    olderStatus: snapshot.olderStatus,
    connection: snapshot.connection,
    hasEarlier: snapshot.hasEarlier,
    subscribe: () => () => {},
    getSnapshot: () => snapshot,
    start: () => {},
    retry: () => {},
    dispose: () => {},
    send: async () => {},
    sendNow: async () => {},
    cancel: async () => {},
    stop: async () => {},
    loadOlder: async () => {},
  };
}

function StopControl() {
  const stop = usePendingPermissionCancellation();
  if (!stop) return null;
  return (
    <>
      <button type="button" onClick={() => { void stop.cancel(); }}>Stop</button>
      {stop.error ? <p role="alert">{stop.error}</p> : null}
    </>
  );
}

const roots: Root[] = [];
function mount(api: Pick<EngineApi, "getPermissions" | "cancelOwned">, rows?: ChatRow[]) {
  const store = fakeStore(rows);
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  act(() => root.render(
    <ChatStoreProvider store={store}>
      <PermissionCancellationProvider api={api} sessionId="session-1" store={store}>
        <StopControl />
      </PermissionCancellationProvider>
    </ChatStoreProvider>,
  ));
  return { root, container };
}

afterEach(() => {
  act(() => roots.splice(0).forEach((root) => root.unmount()));
  document.body.innerHTML = "";
});

describe("permission cancellation provider", () => {
  it("withholds Stop for a clipped prompt until its exact details are visible", () => {
    const revision = "a".repeat(64);
    const marker = {
      kind: "chat-stream-part-preview" as const,
      session_id: "session-1",
      row_id: "prompt-row",
      part_index: 0,
      output_bytes: 200_000,
      output_chars: 200_000,
      output_type: "json" as const,
      output_revision: revision,
      preview: "clipped request",
    };
    const row = {
      id: "prompt-row", seq: 2, kind: "prompt", role: "system", turn_id: null, status: "open",
      parts: [{ type: "prompt", prompt_id: "permission-1", data: promptArgs, chat_stream_part_preview: marker }],
      created_at: "2026-11-25T00:00:00Z",
    } as unknown as ChatRow;
    const api = {
      getPermissions: vi.fn(async () => ({ pending_prompts: [promptArgs] })),
      cancelOwned: vi.fn(async () => ({ cancelled: true })),
    } as unknown as Pick<EngineApi, "getPermissions" | "cancelOwned">;
    const { container } = mount(api, [row]);
    const readyKey = { sessionId: marker.session_id, promptId: "permission-1", revision };

    expect(container.querySelector("button")).toBeNull();
    act(() => markPermissionPromptPreviewVisible({ ...readyKey, revision: "b".repeat(64) }, true));
    expect(container.querySelector("button")).toBeNull();
    act(() => markPermissionPromptPreviewVisible(readyKey, true));
    expect(container.querySelector("button")?.textContent).toBe("Stop");
  });

  it("exposes Stop for a recovered prompt from the currently visible paged row", async () => {
    const revision = "c".repeat(64);
    const marker = {
      kind: "chat-stream-parts-preview" as const,
      session_id: "session-1",
      row_id: "paged-permission-row",
      part_count: 1,
      output_bytes: 300_000,
      output_chars: 280_000,
      output_type: "json" as const,
      output_revision: revision,
    };
    const row: ChatRow = {
      id: marker.row_id,
      seq: 2,
      kind: "prompt",
      role: "system",
      turn_id: null,
      status: "open",
      parts: [{ type: "text", text: "[Large chat row]" }],
      chat_stream_parts_preview: marker,
      created_at: "2026-11-25T00:00:00Z",
    };
    const recovered = {
      sessionId: marker.session_id,
      rowId: marker.row_id,
      revision,
      partIndex: 0,
      promptId: promptArgs.permission_id,
      data: promptArgs,
    };
    const api = {
      getPermissions: vi.fn(async () => ({ pending_prompts: [promptArgs] })),
      cancelOwned: vi.fn(async () => ({ cancelled: true })),
    } as unknown as Pick<EngineApi, "getPermissions" | "cancelOwned">;
    act(() => registerRecoveredPermissionPrompt(recovered, true));
    const { container } = mount(api, [row]);

    try {
      expect(container.querySelector("button")?.textContent).toBe("Stop");
      await act(async () => { container.querySelector("button")?.click(); });
      expect(api.cancelOwned).toHaveBeenCalledWith("session-1", "parent-1");
    } finally {
      act(() => registerRecoveredPermissionPrompt(recovered, false));
    }
  });

  it("does not cancel a pending permission after its session unmounts during lookup", async () => {
    let resolvePermissions!: (value: { pending_prompts: unknown[] }) => void;
    const api = {
      getPermissions: vi.fn(() => new Promise<{ pending_prompts: unknown[] }>((resolve) => { resolvePermissions = resolve; })),
      cancelOwned: vi.fn(async () => ({ cancelled: true })),
    } as unknown as Pick<EngineApi, "getPermissions" | "cancelOwned">;
    const { root, container } = mount(api);
    expect(container.querySelector("button")).not.toBeNull();

    await act(async () => {
      container.querySelector("button")?.click();
      await Promise.resolve();
    });
    expect(api.getPermissions).toHaveBeenCalledOnce();
    act(() => root.unmount());

    await act(async () => {
      resolvePermissions({ pending_prompts: [promptArgs] });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.cancelOwned).not.toHaveBeenCalled();
  });

  it.each(["lookup", "cancel"] as const)("shows an actionable error when permission %s fails", async (failure) => {
    const api = {
      getPermissions: failure === "lookup"
        ? vi.fn(async () => { throw new Error("offline"); })
        : vi.fn(async () => ({ pending_prompts: [promptArgs] })),
      cancelOwned: failure === "cancel"
        ? vi.fn(async () => { throw new Error("offline"); })
        : vi.fn(async () => ({ cancelled: true })),
    } as unknown as Pick<EngineApi, "getPermissions" | "cancelOwned">;
    const { container } = mount(api);
    expect(container.querySelector("button")).not.toBeNull();

    await act(async () => { container.querySelector("button")?.click(); });
    await vi.waitFor(() => expect(container.querySelector('[role="alert"]')).not.toBeNull());
    expect(container.querySelector('[role="alert"]')?.textContent).toBe(
      "Could not stop the pending permission. Try again.",
    );
  });
});
