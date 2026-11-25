// @vitest-environment jsdom
// The composer's Send, Queue Message, Stop and Send Now go through the chat store (#1451, lane 4e).
// The runtime here is a spy: a send or stop that still goes through it is a regression.
import { act, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { AssistantRuntimeProvider, useAui, useExternalStoreRuntime } from "@assistant-ui/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { toRepository } from "../src/chat-runtime";
import { ChatStoreProvider, type ChatSnapshot, type LiveStore } from "../src/chat-store";
import type { ChatRow, InputInfo } from "../src/chat-stream";
import { Thread } from "../src/components/assistant-ui/thread";

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

function fakeStore(rows: ChatRow[], inputs: Record<string, InputInfo>, running: boolean) {
  const snap: ChatSnapshot = { rows, inputs, running, connection: "live", hasEarlier: false, olderStatus: "idle" };
  const store = {
    subscribe: (_listener: () => void) => () => {},
    getSnapshot: () => snap,
    send: vi.fn(async () => {}),
    sendNow: vi.fn(async () => {}),
    cancel: vi.fn(async () => {}),
    stop: vi.fn(async () => {}),
    loadOlder: vi.fn(async () => {}),
    retry: vi.fn(),
    start: vi.fn(),
    dispose: vi.fn(),
  };
  return { store, live: store as unknown as LiveStore };
}

type Spies = {
  onNew: ReturnType<typeof vi.fn>;
  onCancel: ReturnType<typeof vi.fn>;
  onQueueBusyMessage: ReturnType<typeof vi.fn>;
};

const spies = (): Spies => ({
  onNew: vi.fn(),
  onCancel: vi.fn(),
  // The batch submit must never run from these controls; if it does, it fails loudly.
  onQueueBusyMessage: vi.fn(async () => {
    throw new Error("the batch submit was called");
  }),
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

function mount(live: LiveStore, runtimeSpies: Spies): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  containers.push(container);
  const root = createRoot(container);
  roots.push(root);
  act(() => {
    root.render(
      <ChatStoreProvider store={live}>
        <SpyRuntime store={live} spies={runtimeSpies}>
          <Capture />
          <Thread commands={[]} onQueueBusyMessage={runtimeSpies.onQueueBusyMessage} />
        </SpyRuntime>
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
  it("while running with content, Send calls store.send once with a new id and never calls the batch submit", async () => {
    const { store, live } = fakeStore([], {}, true);
    const runtimeSpies = spies();
    const container = mount(live, runtimeSpies);

    await typeText("hello");
    await click(byLabel(container, "Send message while generating"));

    expect(runtimeSpies.onQueueBusyMessage).not.toHaveBeenCalled();
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

  it("Stop calls store.stop and not the runtime's cancel", async () => {
    const { store, live } = fakeStore([], {}, true);
    const runtimeSpies = spies();
    const container = mount(live, runtimeSpies);

    await click(byLabel(container, "Stop generating"));

    expect(store.stop).toHaveBeenCalledTimes(1);
    expect(runtimeSpies.onCancel).not.toHaveBeenCalled();
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
