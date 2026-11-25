// @vitest-environment jsdom
import { act } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatStoreProvider, type ChatSnapshot, type LiveStore } from "@/chat-store";
import type { ChatRow, InputInfo } from "@/chat-stream";
import {
  QueuePanel,
  type WebQueueBatchSnapshot,
  type WebQueueItemSnapshot,
  type WebQueueSnapshot,
} from "@/components/web-queue/QueuePanel";

const item = (
  request_id: string,
  text_preview: string,
  status: WebQueueItemSnapshot["status"] = "queued",
  attachment_labels: string[] = [],
): WebQueueItemSnapshot => ({
  request_id,
  status,
  attempt_id: status === "queued" ? null : `attempt-${request_id}`,
  text_preview,
  attachment_labels,
});

const batch = (
  batch_id: string,
  sequence: number,
  request_ids: string[],
  items: WebQueueItemSnapshot[],
  status: WebQueueBatchSnapshot["status"] = "queued",
): WebQueueBatchSnapshot => ({
  batch_id,
  sequence,
  status,
  request_ids,
  claim_id: status === "queued" ? null : `claim-${batch_id}`,
  items,
});

const snapshot = (batches: WebQueueBatchSnapshot[]): WebQueueSnapshot => ({
  scope_id: "scope-test",
  incarnation: "incarnation-test",
  revision: 4,
  batches,
});

const render = (value: WebQueueSnapshot | null, options?: { busy?: boolean; dispatchAvailable?: boolean; stale?: boolean; refreshError?: boolean }) =>
  renderToStaticMarkup(
    <QueuePanel
      snapshot={value}
      dispatchAvailable={options?.dispatchAvailable ?? true}
      stale={options?.stale ?? false}
      refreshError={options?.refreshError ?? false}
      busy={options?.busy ?? true}
      onRefresh={vi.fn()}
      onRemoveItem={vi.fn()}
      onSendNow={vi.fn()}
    />,
  );

describe("QueuePanel", () => {
  it("renders nothing when there are no queued request IDs", () => {
    expect(render(snapshot([]))).toBe("");
    expect(render(null)).toBe("");
  });

  it("shows text previews and attachment basenames in daemon FIFO order", () => {
    const html = render(snapshot([
      batch("later", 9, ["r3"], [item("r3", "Third batch message")]),
      batch("first", 2, ["r1", "r2"], [
        item("r1", "First in this batch", "queued", ["/scratch/private/retry-trace.txt"]),
        item("r2", "Second in this batch"),
      ]),
    ]));
    const firstBatch = html.indexOf("First in this batch");
    const secondBatchItem = html.indexOf("Second in this batch");
    const laterBatch = html.indexOf("Third batch message");
    expect(firstBatch).toBeGreaterThan(-1);
    expect(firstBatch).toBeLessThan(secondBatchItem);
    expect(secondBatchItem).toBeLessThan(laterBatch);
    expect(html).toContain("retry-trace.txt");
    expect(html).not.toContain("/scratch/private");
    expect(html).toContain("aria-label=\"Send 2 queued messages now\"");
    expect(html).toContain("aria-label=\"Remove queued message 1\"");
    expect(html).toContain("aria-label=\"Remove queued message 2\"");
    expect(html).toMatch(/aria-live="polite"/);
  });

  it("never renders raw payload, attachment refs, claim IDs, attempt IDs, or request IDs", () => {
    const unsafe = {
      ...item("request-id-private", "Visible preview"),
      payload_b64: "RAW_ATTACHMENT_BYTES_SECRET",
      attachment_refs: ["opaque-staged-ref-secret"],
    } as unknown as WebQueueItemSnapshot;
    const html = render(snapshot([
      batch("batch-id-private", 1, ["request-id-private"], [unsafe]),
    ]));
    for (const value of [
      "RAW_ATTACHMENT_BYTES_SECRET",
      "opaque-staged-ref-secret",
      "batch-id-private",
      "claim-batch-id-private",
      "attempt-request-id-private",
      "request-id-private",
    ]) {
      expect(html).not.toContain(value);
    }
    expect(html).toContain("Visible preview");
  });

  it("bounds long text previews without splitting Unicode code points", () => {
    const text = `${"a".repeat(239)}🌱${"b".repeat(20)}`;
    const html = render(snapshot([batch("b", 1, ["r"], [item("r", text)])]));
    expect(html).toContain(`${"a".repeat(239)}🌱…`);
    expect(html).not.toContain("b".repeat(20));
  });

  it("keeps removal available for queued items while idle and hides Send Now", () => {
    const html = render(snapshot([batch("b", 1, ["r"], [item("r", "Queued text")])]), { busy: false });
    expect(html).toContain("Queued for the next turn.");
    expect(html).not.toContain("Send Now");
    expect(html).toContain("Remove");
    expect(html).not.toMatch(/<button[^>]* disabled=""/);
  });

  it("disables Send Now when dispatch is unavailable and tells the truth", () => {
    const html = render(snapshot([batch("b", 1, ["r"], [item("r", "Queued text")])]), {
      busy: true,
      dispatchAvailable: false,
    });
    expect(html).toContain("Queue delivery is unavailable right now.");
    expect(html).toContain("aria-label=\"Send Now unavailable: queue delivery is unavailable\"");
    expect(html).toMatch(/<button[^>]*disabled=""/);
    expect(html).toContain("Remove");
  });

  it("labels stale rows as last known and disables queue mutation until refresh", () => {
    const html = render(snapshot([batch("b", 1, ["r"], [item("r", "Last known message")])]), {
      stale: true,
      refreshError: true,
    });
    expect(html).toContain("Queue status is out of date. Showing last known messages");
    expect(html).toContain("Last known message");
    expect(html).toContain('data-stale="true"');
    expect(html).toContain("Retry");
    expect(html).toContain('aria-label="Send Now unavailable: queue status is out of date"');
    expect((html.match(/<button[^>]*disabled=""/g) ?? []).length).toBe(2);

    const empty = render(null, { stale: true, refreshError: true });
    expect(empty).toContain("Could not load queued messages.");
    expect(empty).toContain("Retry");
  });

  it("holds dispatching and in-doubt items and explains ambiguous delivery", () => {
    for (const status of ["dispatching", "in_doubt"] as const) {
      const html = render(snapshot([
        batch("b", 1, ["r"], [item("r", "Held text", status)], status),
      ]));
      expect(html).toContain(status === "dispatching" ? "Sending" : "Delivery status unknown");
      if (status === "in_doubt") expect(html).toContain("held to avoid sending twice");
      expect(html).toContain(status === "dispatching"
        ? "aria-label=\"Send Now unavailable: earlier message is already being sent\""
        : "aria-label=\"Send Now unavailable: earlier message delivery status is unknown\"");
      expect(html).toContain("aria-label=\"Remove queued message 1\"");
      expect((html.match(/<button[^>]*disabled=""/g) ?? []).length).toBe(2);
    }
  });

  it("fails closed when a batch lists a request without an item snapshot", () => {
    const html = render(snapshot([
      batch("b", 1, ["r", "missing"], [item("r", "Visible preview")]),
    ]));
    expect(html).toContain("Some queued message details are unavailable.");
    expect(html).toContain("1 message is unavailable.");
    expect(html).toContain("aria-label=\"Send Now unavailable: refresh the queue before sending\"");
    expect(html).toContain("aria-label=\"Remove queued message 1\"");
    expect(html).toContain("aria-label=\"Remove queued message 2\"");
    expect((html.match(/<button[^>]*disabled=""/g) ?? []).length).toBe(3);
  });

  it("offers one Send Now control for only the earliest queued batch", () => {
    const html = render(snapshot([
      batch("later", 2, ["r2"], [item("r2", "Later batch")]),
      batch("first", 1, ["r1"], [item("r1", "First batch")]),
    ]));
    expect(html.match(/aria-label="Send 1 queued message now"/g) ?? []).toHaveLength(1);
    expect(html.indexOf("Send 1 queued message now")).toBeLessThan(html.indexOf("First batch"));
    expect(html.indexOf("First batch")).toBeLessThan(html.indexOf("Later batch"));
  });

  it("renders batches as one compact flat message list", () => {
    const html = render(snapshot([
      batch("first", 1, ["r1", "r2"], [item("r1", "First message"), item("r2", "Second message")]),
      batch("later", 2, ["r3"], [item("r3", "Later message")]),
    ]));
    expect(html).not.toContain('data-slot="web-queue-batch"');
    expect(html.match(/data-slot="web-queue-item"/g) ?? []).toHaveLength(3);
    expect(html).not.toMatch(/<span[^>]*>\d+ messages<\/span>/);
    expect(html).toContain('aria-label="Send 2 queued messages now"');
    expect(html.indexOf("First message")).toBeLessThan(html.indexOf("Second message"));
    expect(html.indexOf("Second message")).toBeLessThan(html.indexOf("Later message"));
  });

  it("does not bypass an ambiguous first batch to send a later batch", () => {
    const html = render(snapshot([
      batch("first", 1, ["r1"], [item("r1", "Uncertain first batch", "in_doubt")], "in_doubt"),
      batch("later", 2, ["r2"], [item("r2", "Held later batch")]),
    ]));
    expect(html).toContain('aria-label="Send Now unavailable: earlier message delivery status is unknown"');
    expect(html).not.toContain('aria-label="Send 1 queued message now"');
    expect(html).toMatch(/<button[^>]*aria-label="Send Now unavailable: earlier message delivery status is unknown"[^>]*disabled=""/);
    expect(html).toContain("held to avoid sending twice");
    expect(html.match(/data-slot="web-queue-status-dot"/g) ?? []).toHaveLength(2);
    expect(html).toContain('data-status="in_doubt"');
  });

  it("explains an earlier dispatching batch in the disabled Send Now name", () => {
    const html = render(snapshot([
      batch("first", 1, ["r1"], [item("r1", "Sending first batch", "dispatching")], "dispatching"),
      batch("later", 2, ["r2"], [item("r2", "Later queued batch")]),
    ]));
    expect(html).toContain('aria-label="Send Now unavailable: earlier message is already being sent"');
    expect(html).not.toContain('aria-label="Send 1 queued message now"');
  });
});

// The panel on the chat store (#1451): it lists the store's queued inputs, and its Remove and Send Now call the store.
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
): ChatSnapshot => ({
  rows,
  inputs,
  running,
  connection: "live",
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

function mountOnStore(live: LiveStore): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  act(() => {
    root.render(
      <ChatStoreProvider store={live}>
        <QueuePanel />
      </ChatStoreProvider>,
    );
  });
  return container;
}

async function clickButton(container: HTMLElement, selector: string) {
  const button = container.querySelector<HTMLButtonElement>(selector);
  expect(button, `${selector} is on screen`).toBeTruthy();
  await act(async () => {
    button!.click();
  });
}

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

  it("Remove cancels that input by its id", async () => {
    const { store, live } = fakeChatStore(chatSnapshot(
      [chatRow("web-a", "First"), chatRow("web-c", "Second")],
      { "web-a": queuedInput("web-a"), "web-c": queuedInput("web-c") },
    ));
    const container = mountOnStore(live);
    await clickButton(container, 'button[aria-label="Remove queued message 2"]');
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
    await clickButton(container, '[data-slot="web-queue-send-now"]');
    expect(store.sendNow).toHaveBeenCalledTimes(1);
    expect(store.sendNow).toHaveBeenCalledWith("web-a");
    expect(store.cancel).not.toHaveBeenCalled();
  });

  it("offers Send Now only while a turn runs", () => {
    const { live } = fakeChatStore(chatSnapshot([chatRow("web-a", "First")], { "web-a": queuedInput("web-a") }));
    const container = mountOnStore(live);
    expect(container.textContent).toContain("Queued messages · 1");
    expect(container.querySelector('[data-slot="web-queue-send-now"]')).toBeNull();
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
});
