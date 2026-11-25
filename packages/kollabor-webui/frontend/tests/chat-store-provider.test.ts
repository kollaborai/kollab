// @vitest-environment jsdom
// ChatStoreProvider and useChatStore: how web UI components reach one session's chat store (#1451).
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatRow } from "../src/chat-stream";
import {
  ChatStoreProvider,
  useChatStore,
  type ChatSnapshot,
  type ChatStoreView,
  type LiveStore,
} from "../src/chat-store";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const row = (id: string): ChatRow => ({
  id,
  seq: 1,
  kind: "input",
  role: "user",
  turn_id: null,
  status: "done",
  parts: [{ type: "text", text: id }],
  created_at: "2026-11-25T00:00:00Z",
});

const snapshot = (rows: ChatRow[], running = false): ChatSnapshot => ({
  rows,
  inputs: {},
  running,
  connection: "live",
  hasEarlier: false,
  olderStatus: "idle",
});

function fakeStore(initial: ChatSnapshot) {
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

let roots: Array<ReturnType<typeof createRoot>> = [];
afterEach(() => {
  act(() => roots.forEach((root) => root.unmount()));
  roots = [];
});

function mount(element: ReturnType<typeof createElement>, onUncaughtError?: (error: unknown) => void) {
  const container = document.createElement("div");
  const root = createRoot(container, onUncaughtError ? { onUncaughtError } : undefined);
  roots.push(root);
  act(() => root.render(element));
  return container;
}

describe("useChatStore", () => {
  it("reads the provided store's snapshot and re-renders when it changes", () => {
    const fake = fakeStore(snapshot([row("web-a")]));
    function Probe() {
      const view = useChatStore();
      return createElement("p", null, `${view.rows.map((r) => r.id).join(",")} running=${view.running}`);
    }
    const container = mount(createElement(ChatStoreProvider, { store: fake.live }, createElement(Probe)));
    expect(container.textContent).toBe("web-a running=false");
    act(() => fake.set(snapshot([row("web-a"), row("web-b")], true)));
    expect(container.textContent).toBe("web-a,web-b running=true");
  });

  it("passes every action to the store it was given", async () => {
    const fake = fakeStore(snapshot([]));
    let view: ChatStoreView | undefined;
    function Probe() {
      view = useChatStore();
      return null;
    }
    mount(createElement(ChatStoreProvider, { store: fake.live }, createElement(Probe)));
    await view!.send({ id: "web-1", text: "hi" });
    await view!.sendNow("web-1");
    await view!.cancel("web-2");
    await view!.stop();
    await view!.loadOlder();
    view!.retry();
    expect(fake.store.send).toHaveBeenCalledWith({ id: "web-1", text: "hi" });
    expect(fake.store.sendNow).toHaveBeenCalledWith("web-1");
    expect(fake.store.cancel).toHaveBeenCalledWith("web-2");
    expect(fake.store.stop).toHaveBeenCalledTimes(1);
    expect(fake.store.loadOlder).toHaveBeenCalledTimes(1);
    expect(fake.store.retry).toHaveBeenCalledTimes(1);
  });

  it("keeps the same view object while the snapshot is unchanged", () => {
    const fake = fakeStore(snapshot([row("web-a")]));
    const seen: ChatStoreView[] = [];
    function Probe() {
      seen.push(useChatStore());
      return null;
    }
    const element = createElement(ChatStoreProvider, { store: fake.live }, createElement(Probe));
    const container = document.createElement("div");
    const root = createRoot(container);
    roots.push(root);
    act(() => root.render(element));
    act(() => root.render(createElement(ChatStoreProvider, { store: fake.live }, createElement(Probe))));
    expect(seen.length).toBeGreaterThanOrEqual(2);
    expect(seen[seen.length - 1]).toBe(seen[0]);
  });

  it("names the missing provider when used without one", () => {
    function Probe() {
      useChatStore();
      return null;
    }
    // act() rethrows an error thrown while rendering, so the render itself throws.
    expect(() => mount(createElement(Probe))).toThrow(/ChatStoreProvider/);
  });
});
