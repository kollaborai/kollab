// @vitest-environment jsdom
// The composer's Send, Queue Message, Stop and Send Now go through the chat store (#1451, lane 4e).
// The runtime here is a spy: a send or stop that still goes through it is a regression.
import { act, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { AssistantRuntimeProvider, useAui, useExternalStoreRuntime } from "@assistant-ui/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { toRepository } from "../src/chat-runtime";
import { ChatStoreProvider, type ChatSnapshot, type LiveStore, type LocalGoalReply } from "../src/chat-store";
import type { ChatRow, InputInfo } from "../src/chat-stream";
import { Thread } from "../src/components/assistant-ui/thread";
import { PermissionCancellationProvider } from "../src/permission-cancellation";
import type { EngineApi } from "../src/api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// jsdom has no ResizeObserver. The thread's viewport only needs the constructor to exist.
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

// jsdom has no Element.scrollTo either; the thread's viewport calls it when messages change.
Element.prototype.scrollTo = () => {};

const row = (id: string, text: string): ChatRow => ({
  id,
  seq: 1,
  kind: "input",
  role: "user",
  turn_id: null,
  status: "done",
  parts: [{ type: "text", text }],
  created_at: "2026-11-25T00:00:00Z",
});

function fakeStore(
  rows: ChatRow[],
  inputs: Record<string, InputInfo>,
  running: boolean,
  goalReplies: Record<string, LocalGoalReply> = {},
) {
  const snap = { rows, inputs, running, connection: "live" as const, hasEarlier: false, olderStatus: "idle" as const, goalReplies } as ChatSnapshot;
  const store = {
    subscribe: (_listener: () => void) => () => {},
    getSnapshot: () => snap,
    send: vi.fn(async () => {}),
    sendNow: vi.fn(async () => {}),
    cancel: vi.fn(async () => {}),
    stop: vi.fn(async () => {}),
    loadOlder: vi.fn(async () => {}),
    retry: vi.fn(),
    checkGoalReply: vi.fn(),
    start: vi.fn(),
    dispose: vi.fn(),
  };
  return { store, live: store as unknown as LiveStore };
}

type Spies = {
  onNew: ReturnType<typeof vi.fn>;
  onCancel: ReturnType<typeof vi.fn>;
};

const spies = (): Spies => ({
  onNew: vi.fn(),
  onCancel: vi.fn(),
});

// The thread's messages come from the store's rows. Send and Stop reach the runtime's
// adapter; the spies stand in for it so the test can see when they are called.
function SpyRuntime({ store, spies: runtimeSpies, children }: { store: LiveStore; spies: Spies; children: ReactNode }) {
  const snap = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const repository = useMemo(() => toRepository(snap.rows), [snap.rows]);
  const runtime = useExternalStoreRuntime({
    messageRepository: repository,
    isRunning: snap.running,
    onNew: async (message) => {
      runtimeSpies.onNew(message);
    },
    onCancel: async () => {
      runtimeSpies.onCancel();
    },
  });
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}

let aui: ReturnType<typeof useAui> | undefined;
function Capture() {
  aui = useAui();
  return null;
}

const roots: Root[] = [];
const containers: HTMLElement[] = [];

function mount(
  live: LiveStore,
  runtimeSpies: Spies,
  permissionApi?: Pick<EngineApi, "getPermissions" | "cancelOwned">,
): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  containers.push(container);
  const root = createRoot(container);
  roots.push(root);
  act(() => {
    root.render(
      <ChatStoreProvider store={live}>
        {permissionApi ? (
          <PermissionCancellationProvider api={permissionApi} sessionId="session-1" store={live}>
            <SpyRuntime store={live} spies={runtimeSpies}>
              <Capture />
              <Thread commands={[]} />
            </SpyRuntime>
          </PermissionCancellationProvider>
        ) : (
          <SpyRuntime store={live} spies={runtimeSpies}>
            <Capture />
            <Thread commands={[]} />
          </SpyRuntime>
        )}
      </ChatStoreProvider>,
    );
  });
  return container;
}

afterEach(() => {
  act(() => roots.splice(0).forEach((root) => root.unmount()));
  containers.splice(0).forEach((container) => container.remove());
  aui = undefined;
});

async function typeText(text: string) {
  await act(async () => {
    aui?.composer.setText(text);
  });
}

async function click(element: Element | null | undefined) {
  expect(element, "the control to click is on screen").toBeTruthy();
  await act(async () => {
    (element as HTMLElement).click();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

const byLabel = (container: HTMLElement, label: string) =>
  container.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);

describe("composer send, queue, Stop and Send Now on the chat store", () => {
  it("while running with content, Send calls store.send once with a new id", async () => {
    const { store, live } = fakeStore([], {}, true);
    const runtimeSpies = spies();
    const container = mount(live, runtimeSpies);

    await typeText("hello");
    await click(byLabel(container, "Send message while generating"));

    expect(store.send).toHaveBeenCalledTimes(1);
    expect(store.send.mock.calls[0]?.[0]).toMatchObject({
      id: expect.stringMatching(/^web-[0-9a-f]{24}$/),
      text: "hello",
    });
    expect(runtimeSpies.onNew).not.toHaveBeenCalled();
  });

  it("while idle, Send calls store.send and not the runtime", async () => {
    const { store, live } = fakeStore([], {}, false);
    const runtimeSpies = spies();
    const container = mount(live, runtimeSpies);

    await typeText("hello");
    await click(byLabel(container, "Send message"));

    expect(store.send).toHaveBeenCalledTimes(1);
    expect(runtimeSpies.onNew).not.toHaveBeenCalled();
  });

  it("a /goal input gets a separate client-side cancellation capability", async () => {
    const { store, live } = fakeStore([], {}, false);
    const runtimeSpies = spies();
    const container = mount(live, runtimeSpies);

    await typeText("/goal inspect the build");
    await click(byLabel(container, "Send message"));

    const sent = store.send.mock.calls[0]?.[0] as { id: string; text: string; goal_reply_id?: string };
    expect(sent.text).toBe("/goal inspect the build");
    expect(sent.goal_reply_id).toMatch(/^goal-[0-9a-f]{32}$/);
    expect(sent.id).toMatch(/^web-[0-9a-f]{24}$/);
    expect(runtimeSpies.onNew).not.toHaveBeenCalled();
  });

  it("reuses the goal capability for a rejected draft retry and makes a new one after edits", async () => {
    const { store, live } = fakeStore([], {}, false);
    store.send
      .mockRejectedValueOnce(new Error("connection lost"))
      .mockRejectedValueOnce(Object.assign(new Error("rejected"), { status: 400 }));
    const container = mount(live, spies());

    await typeText("/goal inspect the build");
    await click(byLabel(container, "Send message"));
    await click(byLabel(container, "Send message"));

    const first = store.send.mock.calls[0]?.[0] as { id: string; goal_reply_id: string };
    const retry = store.send.mock.calls[1]?.[0] as { id: string; goal_reply_id: string };
    expect(retry).toEqual(first);

    await typeText("/goal inspect the docs");
    await click(byLabel(container, "Send message"));
    const edited = store.send.mock.calls[2]?.[0] as { id: string; goal_reply_id: string };
    expect(edited.id).not.toBe(first.id);
    expect(edited.goal_reply_id).not.toBe(first.goal_reply_id);
  });

  it("renders the private goal outcome below only its initiating input row", () => {
    const id = "web-goal-result";
    const { live } = fakeStore(
      [row(id, "/goal inspect the build"), row("other-goal", "hello")],
      {},
      false,
      { [id]: { state: "complete", success: false, message: "The build is red." } },
    );
    const container = mount(live, spies());

    const reply = container.querySelector<HTMLElement>('[data-slot="kollab_goal-reply"]');
    expect(reply?.getAttribute("role")).toBe("alert");
    expect(reply?.textContent).toContain("Goal failed");
    expect(reply?.textContent).toContain("The build is red.");
    expect(container.querySelector('[data-chat-row-id="other-goal"] [data-slot="kollab_goal-reply"]')).toBeNull();
  });

  it("renders unresolved goal admission with a status-only recovery action", async () => {
    const id = "web-goal-uncertain";
    const { store, live } = fakeStore(
      [row(id, "/goal inspect the build")],
      {},
      false,
      { [id]: { state: "uncertain" } },
    );
    const container = mount(live, spies());

    const reply = container.querySelector<HTMLElement>('[data-slot="kollab_goal-reply"]');
    expect(reply?.getAttribute("role")).toBe("status");
    expect(reply?.textContent).toContain("Goal admission could not be confirmed.");
    await click(byLabel(container, "Check status"));

    expect(store.checkGoalReply).toHaveBeenCalledWith(id);
    expect(store.send).not.toHaveBeenCalled();
  });

  it("Stop calls store.stop and not the runtime's cancel", async () => {
    const { store, live } = fakeStore([], {}, true);
    const runtimeSpies = spies();
    const container = mount(live, runtimeSpies);

    await click(byLabel(container, "Stop generating"));

    expect(store.stop).toHaveBeenCalledTimes(1);
    expect(runtimeSpies.onCancel).not.toHaveBeenCalled();
  });

  it("shows a visible error when stopping a pending permission fails", async () => {
    const permissionRow: ChatRow = {
      id: "permission-row", seq: 1, kind: "tool", role: "assistant", turn_id: "turn-1", status: "complete",
      parts: [{
        type: "tool-call", tool_call_id: "call-1", tool_name: "request_permission",
        args: { tool_id: "shell-1", request_id: "parent-1", details: { source: "acp", parked: false } },
      }],
      created_at: "2026-11-25T00:00:00Z",
    };
    const { live } = fakeStore([permissionRow], {}, false);
    const api = {
      getPermissions: vi.fn(async () => { throw new Error("offline"); }),
      cancelOwned: vi.fn(async () => ({ cancelled: true })),
    } as unknown as Pick<EngineApi, "getPermissions" | "cancelOwned">;
    const container = mount(live, spies(), api);

    await click(byLabel(container, "Stop generating"));

    expect(container.querySelector('[role="alert"]')?.textContent).toBe(
      "Could not stop the pending permission. Try again.",
    );
  });

  it("Send Now calls store.sendNow with the queued input's id", async () => {
    const id = `web-${"a".repeat(24)}`;
    const { store, live } = fakeStore([row(id, "later")], { [id]: { id, state: "queued", turn_id: null } }, true);
    const container = mount(live, spies());

    const sendNow = [...container.querySelectorAll("button")].find((button) => button.textContent === "Send Now");
    await click(sendNow);

    expect(store.sendNow).toHaveBeenCalledTimes(1);
    expect(store.sendNow).toHaveBeenCalledWith(id);
  });

  it("shows the queued note on the queued input row only, not on started or joined rows", () => {
    const rows = [row("queued-1", "one"), row("started-1", "two"), row("joined-1", "three")];
    const inputs: Record<string, InputInfo> = {
      "queued-1": { id: "queued-1", state: "queued", turn_id: null },
      "started-1": { id: "started-1", state: "started", turn_id: "turn-1" },
      "joined-1": { id: "joined-1", state: "joined", turn_id: "turn-1" },
    };
    const { live } = fakeStore(rows, inputs, true);
    const container = mount(live, spies());

    const messages = [...container.querySelectorAll('[data-role="user"]')];
    expect(messages).toHaveLength(3);
    expect(messages.map((message) => message.querySelector(".aui-user-message-queued") !== null)).toEqual([
      true,
      false,
      false,
    ]);
  });
});
