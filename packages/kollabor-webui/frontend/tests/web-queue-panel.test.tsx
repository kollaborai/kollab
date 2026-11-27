// @vitest-environment jsdom
// The queue panel reads the queued inputs from the chat store, and its Remove and Send Now call the store (#1451).
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatStoreProvider, type ChatSnapshot, type ConnectionState, type LiveStore } from "@/chat-store";
import type { ChatRow, InputInfo } from "@/chat-stream";
import { QueuePanel } from "@/components/web-queue/QueuePanel";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const chatRow = (id: string, text: string): ChatRow => ({
  id,
  seq: 1,
  kind: "input",
  role: "user",
  turn_id: null,
  status: "done",
  parts: [{ type: "text", text }],
  created_at: "2026-11-25T00:00:00Z",
});

const queuedInput = (id: string): InputInfo => ({ id, state: "queued", turn_id: null });
const joinedInput = (id: string): InputInfo => ({ id, state: "joined", turn_id: "turn-1" });

const chatSnapshot = (
  rows: ChatRow[],
  inputs: Record<string, InputInfo>,
  running = false,
  connection: ConnectionState = "live",
): ChatSnapshot => ({
  rows,
  inputs,
  goalReplies: {},
  running,
  connection,
  hasEarlier: false,
  olderStatus: "idle",
});

// The fake store from chat-store-provider.test.ts: a snapshot that set() replaces, and spies for each action.
function fakeChatStore(initial: ChatSnapshot) {
  let snap = initial;
  const listeners = new Set<() => void>();
  const store = {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
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
  return {
    store,
    live: store as unknown as LiveStore,
    set(next: ChatSnapshot) {
      snap = next;
      listeners.forEach((listener) => listener());
    },
  };
}

const mounted: Root[] = [];
afterEach(() => {
  act(() => mounted.splice(0).forEach((root) => root.unmount()));
  document.body.innerHTML = "";
});

function mountOnStore(live: LiveStore, canSendNow = true): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  act(() => {
    root.render(
      <ChatStoreProvider store={live}>
        <QueuePanel canSendNow={canSendNow} />
      </ChatStoreProvider>,
    );
  });
  return container;
}

async function click(element: HTMLElement | null | undefined) {
  expect(element, "the control to click is on screen").toBeTruthy();
  await act(async () => {
    element!.click();
  });
}

const buttonNamed = (container: HTMLElement, name: string) =>
  [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === name);

describe("QueuePanel on the chat store", () => {
  it("lists each queued input in queue order, with the text of its row", () => {
    // The inputs are keyed out of queue order on purpose: the order must come from the rows.
    const { live } = fakeChatStore(chatSnapshot(
      [chatRow("web-a", "First queued"), chatRow("web-b", "Already running"), chatRow("web-c", "Third queued")],
      { "web-c": queuedInput("web-c"), "web-b": joinedInput("web-b"), "web-a": queuedInput("web-a") },
      true,
    ));
    const container = mountOnStore(live);
    const text = container.textContent ?? "";
    expect(text).toContain("Queued messages · 2");
    expect(container.querySelectorAll('[data-slot="web-queue-item"]')).toHaveLength(2);
    expect(text.indexOf("First queued")).toBeGreaterThan(-1);
    expect(text.indexOf("First queued")).toBeLessThan(text.indexOf("Third queued"));
    expect(text).not.toContain("Already running");
  });

  it("lists a queued input that has no row yet", () => {
    const { live } = fakeChatStore(chatSnapshot([], { "web-x": queuedInput("web-x") }, true));
    const container = mountOnStore(live);
    expect(container.textContent).toContain("Queued messages · 1");
    expect(container.querySelectorAll('[data-slot="web-queue-item"]')).toHaveLength(1);
  });

  it("shows the text of a row, never its other parts or the input id", () => {
    const row: ChatRow = {
      ...chatRow("web-private-id", "Visible preview"),
      parts: [
        { type: "text", text: "Visible preview" },
        { type: "image", url: "https://files.example/secret-ref" },
      ],
    };
    const { live } = fakeChatStore(chatSnapshot([row], { "web-private-id": queuedInput("web-private-id") }, true));
    const container = mountOnStore(live);
    expect(container.textContent).toContain("Visible preview");
    expect(container.innerHTML).not.toContain("secret-ref");
    expect(container.innerHTML).not.toContain("web-private-id");
  });

  it("bounds long text previews without splitting Unicode code points", () => {
    const text = `${"a".repeat(239)}🌱${"b".repeat(20)}`;
    const { live } = fakeChatStore(chatSnapshot([chatRow("web-a", text)], { "web-a": queuedInput("web-a") }, true));
    const container = mountOnStore(live);
    expect(container.textContent).toContain(`${"a".repeat(239)}🌱…`);
    expect(container.textContent).not.toContain("b".repeat(20));
  });

  it("keeps Remove available while idle and offers no Send Now", () => {
    const { live } = fakeChatStore(chatSnapshot([chatRow("web-a", "Queued text")], { "web-a": queuedInput("web-a") }));
    const container = mountOnStore(live);
    expect(container.textContent).toContain("Queued for the next turn.");
    expect(container.querySelector('[data-slot="web-queue-send-now"]')).toBeNull();
    expect(container.querySelector<HTMLButtonElement>('button[aria-label="Remove queued message 1"]')?.disabled).toBe(false);
  });

  it("Remove cancels that input by its id", async () => {
    const { store, live } = fakeChatStore(chatSnapshot(
      [chatRow("web-a", "First"), chatRow("web-c", "Second")],
      { "web-a": queuedInput("web-a"), "web-c": queuedInput("web-c") },
    ));
    const container = mountOnStore(live);
    await click(container.querySelector('button[aria-label="Remove queued message 2"]'));
    expect(store.cancel).toHaveBeenCalledTimes(1);
    expect(store.cancel).toHaveBeenCalledWith("web-c");
  });

  it("Send Now sends the earliest queued input by its id", async () => {
    const { store, live } = fakeChatStore(chatSnapshot(
      [chatRow("web-a", "First"), chatRow("web-c", "Second")],
      { "web-a": queuedInput("web-a"), "web-c": queuedInput("web-c") },
      true,
    ));
    const container = mountOnStore(live);
    const sendNow = container.querySelector('[data-slot="web-queue-send-now"]');
    expect(sendNow?.getAttribute("aria-label")).toBe("Send 1 queued message now");
    await click(sendNow as HTMLElement | null);
    expect(store.sendNow).toHaveBeenCalledTimes(1);
    expect(store.sendNow).toHaveBeenCalledWith("web-a");
    expect(store.cancel).not.toHaveBeenCalled();
  });

  it("hides Send Now for a running harness session", () => {
    const { live } = fakeChatStore(chatSnapshot(
      [chatRow("web-a", "Queued text")],
      { "web-a": queuedInput("web-a") },
      true,
    ));
    const container = mountOnStore(live, false);
    expect(container.querySelector('[data-slot="web-queue-send-now"]')).toBeNull();
    expect(container.textContent).toContain("Waiting for this turn to finish.");
  });

  it("shows nothing when no input is queued", () => {
    const running = fakeChatStore(chatSnapshot(
      [chatRow("web-a", "Running now")],
      { "web-a": { id: "web-a", state: "started", turn_id: "turn-1" } },
      true,
    ));
    expect(mountOnStore(running.live).innerHTML).toBe("");
    const empty = fakeChatStore(chatSnapshot([], {}));
    expect(mountOnStore(empty.live).innerHTML).toBe("");
  });

  it("drops an input from the list when the store reports it started", () => {
    const fake = fakeChatStore(chatSnapshot([chatRow("web-a", "First")], { "web-a": queuedInput("web-a") }, true));
    const container = mountOnStore(fake.live);
    expect(container.querySelectorAll('[data-slot="web-queue-item"]')).toHaveLength(1);
    act(() => fake.set(chatSnapshot([chatRow("web-a", "First")], { "web-a": joinedInput("web-a") }, true)));
    expect(container.innerHTML).toBe("");
  });

  it("disables Send Now while the stream reconnects, and shows no error for the drop", () => {
    const { live } = fakeChatStore(chatSnapshot(
      [chatRow("web-a", "First")],
      { "web-a": queuedInput("web-a") },
      true,
      "reconnecting",
    ));
    const container = mountOnStore(live);
    expect(container.textContent).toContain("Queue delivery is unavailable right now.");
    const sendNow = container.querySelector<HTMLButtonElement>('[data-slot="web-queue-send-now"]');
    expect(sendNow?.getAttribute("aria-label")).toBe("Send Now unavailable: queue delivery is unavailable");
    expect(sendNow?.disabled).toBe(true);
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("says the list is out of date when the stream fails, holds changes, and offers Retry", async () => {
    const fake = fakeChatStore(chatSnapshot(
      [chatRow("web-a", "Last known")],
      { "web-a": queuedInput("web-a") },
      true,
      "failed",
    ));
    const container = mountOnStore(fake.live);
    expect(container.textContent).toContain(
      "Queue status is out of date. Showing last known messages; refresh before changing the queue.",
    );
    expect(container.textContent).toContain("Last known");
    expect(container.querySelector('[data-stale="true"]')).not.toBeNull();
    expect(container.querySelector<HTMLButtonElement>('[data-slot="web-queue-send-now"]')?.disabled).toBe(true);
    expect(container.querySelector<HTMLButtonElement>('button[aria-label="Remove queued message 1"]')?.disabled).toBe(true);
    await click(buttonNamed(container, "Retry"));
    expect(fake.store.retry).toHaveBeenCalledTimes(1);
  });

  it("leaves a failed stream to the chat's own notice when nothing is queued", () => {
    const fake = fakeChatStore(chatSnapshot([], {}, false, "failed"));
    const container = mountOnStore(fake.live);
    expect(container.querySelector('[data-slot="web-queue-panel"]')).toBeNull();
  });

  it("shows an error line when the store refuses a Remove", async () => {
    const { store, live } = fakeChatStore(chatSnapshot([chatRow("web-a", "First")], { "web-a": queuedInput("web-a") }));
    store.cancel.mockRejectedValueOnce(new Error("refused"));
    const container = mountOnStore(live);
    await click(container.querySelector('button[aria-label="Remove queued message 1"]'));
    expect(container.textContent).toContain("Could not update the queue. Try again.");
  });
});
