import assert from "node:assert/strict";
import { afterEach, expect, test, vi } from "vitest";
import { ApiError, EngineApi, type WebMessageQueueSnapshot } from "../src/api.ts";
import { WebQueuePanelController } from "../src/components/web-queue/WebQueuePanelMount.tsx";
import {
  acknowledgeBusyWebMessage,
  hasUnresolvedBusyWebMessage,
  QueueSubmissionReconciliationError,
  submitBusyWebMessage,
  type QueueSubmissionLocks,
  type BusyWebMessageAttachment,
  type BusyWebMessageInput,
} from "../src/components/web-queue/submit.ts";

class MemoryStorage {
  values = new Map<string, string>();
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}

class SerialQueueLocks implements QueueSubmissionLocks {
  private readonly tails = new Map<string, Promise<void>>();

  async request<T>(name: string, callback: () => Promise<T>): Promise<T> {
    const previous = this.tails.get(name) ?? Promise.resolve();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const queued = previous.then(() => gate);
    this.tails.set(name, queued);
    await previous;
    try {
      return await callback();
    } finally {
      release();
      if (this.tails.get(name) === queued) this.tails.delete(name);
    }
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("queue API methods preserve Unicode text, opaque IDs, and staged image bytes", async () => {
  const requests: { url: string; method: string; body: unknown; headers: Headers }[] = [];
  vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/config") return new Response("{}", { status: 200 });
    requests.push({
      url,
      method: init?.method ?? "GET",
      body: init?.body,
      headers: new Headers(init?.headers),
    });
    if (url.includes("/send-now?")) {
      return new Response(JSON.stringify({ detail: "Queue dispatch is unavailable; the message remains queued." }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });

  const api = new EngineApi("http://engine");
  await api.allocateWebMessageQueueIds("session/one", "conversation-a", 1, 2, 1);
  const image = new Blob([new Uint8Array([0, 1, 2, 255])], { type: "image/png" });
  await api.stageWebMessageQueueAttachment("session/one", "conversation-a", "att-op-1", image);
  await api.enqueueWebMessageBatch("session/one", {
    conversation_uid: "conversation-a",
    operation_id: "op-1",
    batch_id: "batch-1",
    items: [{ request_id: "item-1", text: "café 你好", attachment_refs: ["att-ref"] }],
  });

  assert.equal(requests[0]?.url, "http://engine/sessions/session%2Fone/web-message-queue/ids");
  assert.deepEqual(JSON.parse(String(requests[0]?.body)), {
    conversation_uid: "conversation-a",
    item_count: 1,
    operation_count: 2,
    attachment_operation_count: 1,
  });
  assert.equal(requests[1]?.method, "PUT");
  assert.equal(requests[1]?.headers.get("Content-Type"), "image/png");
  assert.equal(requests[1]?.body, image);
  assert.equal(requests[2]?.url, "http://engine/sessions/session%2Fone/web-message-queue/batches");
  assert.deepEqual(JSON.parse(String(requests[2]?.body)), {
    conversation_uid: "conversation-a",
    operation_id: "op-1",
    batch_id: "batch-1",
    items: [{ request_id: "item-1", text: "café 你好", attachment_refs: ["att-ref"] }],
  });
});

test("queue API methods encode conversation preconditions and remove one ID only", async () => {
  const requests: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/config") return new Response("{}", { status: 200 });
    requests.push({ url, method: init?.method ?? "GET", body: init?.body });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });

  const api = new EngineApi("http://engine");
  await api.getWebMessageQueue("session one", "conversation/a");
  await api.removeWebMessageQueueItem("session one", "conversation/a", "op-remove", "item/one");
  await api.getWebMessageQueueAttachment("session one", "conversation/a", "item/one", "att/ref");

  assert.equal(requests[0]?.url, "http://engine/sessions/session%20one/web-message-queue?conversation_uid=conversation%2Fa");
  assert.equal(requests[1]?.url, "http://engine/sessions/session%20one/web-message-queue/items/item%2Fone?conversation_uid=conversation%2Fa&operation_id=op-remove");
  assert.equal(requests[1]?.method, "DELETE");
  assert.equal(requests[2]?.url, "http://engine/sessions/session%20one/web-message-queue/items/item%2Fone/attachments/att%2Fref?conversation_uid=conversation%2Fa");
});

test("Send Now exposes a truthful unavailable response until a dispatcher exists", async () => {
  vi.stubGlobal("fetch", async (input: string | URL | Request) => {
    const url = String(input);
    if (url === "/api/config") return new Response("{}", { status: 200 });
    return new Response(JSON.stringify({ detail: "Queue dispatch is unavailable; the message remains queued." }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  });

  const api = new EngineApi("http://engine");
  await assert.rejects(
    api.sendWebMessageQueueBatchNow("s1", "conversation-a", "batch-1"),
    (error: unknown) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, 503);
      assert.match(error.message, /remains queued/);
      return true;
    },
  );
});

const queueSnapshot = (
  scopeId: string,
  incarnation: string,
  revision: number,
  batches: WebMessageQueueSnapshot["batches"] = [],
  dispatchAvailable = true,
): WebMessageQueueSnapshot => ({
  scope_id: scopeId,
  incarnation,
  revision,
  batches,
  dispatch_available: dispatchAvailable,
});

const queuedBatch: WebMessageQueueSnapshot["batches"][number] = {
  batch_id: "batch-1",
  sequence: 1,
  status: "queued",
  request_ids: ["item-1"],
  claim_id: null,
  items: [{
    request_id: "item-1",
    status: "queued",
    attempt_id: null,
    text_preview: "Queued text",
    attachment_labels: ["diagram.png"],
  }],
};

test("queue panel controller refreshes on mount, polls, and fences old session responses", async () => {
  const pending: Array<{ sessionId: string; uid: string; resolve: (snapshot: WebMessageQueueSnapshot) => void }> = [];
  const api = {
    getWebMessageQueue: vi.fn((sessionId: string, uid: string) => new Promise<WebMessageQueueSnapshot>((resolve) => {
      pending.push({ sessionId, uid, resolve });
    })),
  } as unknown as EngineApi;
  const scheduled = new Map<number, () => void>();
  let timerId = 0;
  const setTimer = ((callback: TimerHandler) => {
    const id = ++timerId;
    scheduled.set(id, callback as () => void);
    return id;
  }) as typeof globalThis.setInterval;
  const clearTimer = ((id: ReturnType<typeof globalThis.setInterval>) => {
    scheduled.delete(id as number);
  }) as typeof globalThis.clearInterval;
  const controller = new WebQueuePanelController(api, setTimer, clearTimer);

  controller.setIdentity("session/old", "conversation-old");
  await Promise.resolve();
  controller.setIdentity("session/new", "conversation-new");
  await Promise.resolve();
  assert.deepEqual(pending.map(({ sessionId, uid }) => [sessionId, uid]), [
    ["session/old", "conversation-old"],
    ["session/new", "conversation-new"],
  ]);
  pending[1]!.resolve(queueSnapshot("scope-new", "inc-new", 2, [queuedBatch]));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(controller.getState().snapshot?.scope_id, "scope-new");
  pending[0]!.resolve(queueSnapshot("scope-old", "inc-old", 99, [queuedBatch]));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(controller.getState().snapshot?.scope_id, "scope-new", "late response from a prior conversation replaced the current queue");

  const activeTimer = [...scheduled.values()][0];
  assert.ok(activeTimer, "the active conversation must poll for updates from other clients");
  activeTimer();
  await Promise.resolve();
  assert.equal(pending.at(-1)?.sessionId, "session/new");
  assert.equal(pending.at(-1)?.uid, "conversation-new");
  pending.at(-1)!.resolve(queueSnapshot("scope-new", "inc-new", 3, [queuedBatch]));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(controller.getState().snapshot?.revision, 3);
});

test("queue controller removes one item with a daemon-minted operation and sends only the revalidated FIFO batch", async () => {
  const requests: Array<{ url: string; method: string; auth: string | null; body: string | null }> = [];
  let removed = false;
  let dispatches = 0;
  let revision = 2;
  vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/config") return new Response("{}", { status: 200 });
    const method = init?.method ?? "GET";
    requests.push({
      url,
      method,
      auth: new Headers(init?.headers).get("Authorization"),
      body: typeof init?.body === "string" ? init.body : null,
    });
    if (url.endsWith("/ids")) {
      return Response.json({ scope_id: "scope-a", incarnation: "inc-a", operation_ids: ["op-remove"] });
    }
    if (method === "DELETE") {
      removed = true;
      return Response.json({ status: "removed", request_ids: ["item-1"] });
    }
    if (url.includes("/send-now?")) {
      dispatches += 1;
      return Response.json({ status: "admitted", accepted: true, request_ids: ["item-1"] });
    }
    if (method === "GET") {
      const snapshot = queueSnapshot("scope-a", "inc-a", ++revision, removed ? [] : [queuedBatch]);
      return Response.json(snapshot);
    }
    throw new Error(`Unexpected queue request: ${method} ${url}`);
  });

  const api = new EngineApi("http://engine");
  api.setToken("test-auth-token");
  const controller = new WebQueuePanelController(api, (() => 1) as unknown as typeof globalThis.setInterval);
  controller.setIdentity("session/one", "conversation-a");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(controller.getState().snapshot?.scope_id, "scope-a");

  await controller.removeItem("item-1");
  assert.equal(controller.getState().snapshot?.batches.length, 0, "removal did not refresh the visible snapshot");
  const operationRequest = requests.find(({ url }) => url.endsWith("/ids"));
  assert.deepEqual(JSON.parse(operationRequest?.body ?? "{}"), {
    conversation_uid: "conversation-a",
    item_count: 1,
    operation_count: 1,
    attachment_operation_count: 0,
  });
  const deleteRequest = requests.find(({ method }) => method === "DELETE");
  assert.match(deleteRequest?.url ?? "", /items\/item-1\?conversation_uid=conversation-a&operation_id=op-remove$/);
  assert.equal(deleteRequest?.auth, "Bearer test-auth-token");

  removed = false;
  await controller.refresh(true);
  await controller.sendNow("batch-1");
  assert.equal(dispatches, 1, "Send Now was retried or omitted");
  const sendRequest = requests.find(({ url }) => url.includes("/send-now?"));
  assert.match(sendRequest?.url ?? "", /batches\/batch-1\/send-now\?conversation_uid=conversation-a$/);
  assert.equal(sendRequest?.auth, "Bearer test-auth-token");
  assert.equal(controller.getState().snapshot?.scope_id, "scope-a");
});

test("queue controller refuses to claim Send Now succeeded when dispatch was not accepted", async () => {
  const requests: string[] = [];
  vi.stubGlobal("fetch", async (input: string | URL | Request) => {
    const url = String(input);
    if (url === "/api/config") return new Response("{}", { status: 200 });
    requests.push(url);
    if (url.includes("/send-now?")) return Response.json({ status: "busy", accepted: false });
    return Response.json(queueSnapshot("scope-a", "inc-a", 1, [queuedBatch]));
  });
  const api = new EngineApi("http://engine");
  const controller = new WebQueuePanelController(api, (() => 1) as unknown as typeof globalThis.setInterval);
  controller.setIdentity("session-1", "conversation-a");
  await new Promise((resolve) => setTimeout(resolve, 0));
  await assert.rejects(controller.sendNow("batch-1"), /did not accept/);
  assert.equal(requests.filter((url) => url.includes("/send-now?")).length, 1, "an ambiguous/refused Send Now was automatically retried");
});

test("queue controller rechecks that Send Now still targets the FIFO head", async () => {
  const requests: string[] = [];
  let reads = 0;
  const blockedHead = {
    ...queuedBatch,
    batch_id: "new-earlier-batch",
    sequence: 1,
    status: "in_doubt" as const,
    claim_id: "uncertain-claim",
    request_ids: ["uncertain-request"],
    items: [{
      request_id: "uncertain-request",
      status: "in_doubt" as const,
      attempt_id: "uncertain-attempt",
      text_preview: "Earlier uncertain item",
      attachment_labels: [],
    }],
  };
  vi.stubGlobal("fetch", async (input: string | URL | Request) => {
    const url = String(input);
    if (url === "/api/config") return new Response("{}", { status: 200 });
    requests.push(url);
    if (url.includes("/send-now?")) return Response.json({ status: "admitted", accepted: true });
    if (url.includes("/web-message-queue?")) {
      reads += 1;
      return Response.json(queueSnapshot(
        "scope-a",
        "inc-a",
        reads,
        reads === 1 ? [queuedBatch] : [blockedHead, queuedBatch],
      ));
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  const api = new EngineApi("http://engine");
  const controller = new WebQueuePanelController(api, (() => 1) as unknown as typeof globalThis.setInterval);
  controller.setIdentity("session-1", "conversation-a");
  await new Promise((resolve) => setTimeout(resolve, 0));
  await assert.rejects(controller.sendNow("batch-1"), /no longer ready/);
  assert.equal(requests.filter((url) => url.includes("/send-now?")).length, 0,
    "a newly ambiguous FIFO head must block sending a later batch");
});

test("Send Now tolerates a concurrent background refresh of the same queue", async () => {
  let reads = 0;
  let resolvePreflight!: (snapshot: WebMessageQueueSnapshot) => void;
  let sends = 0;
  const api = {
    getWebMessageQueue: vi.fn(() => {
      reads += 1;
      if (reads === 1) return Promise.resolve(queueSnapshot("scope-a", "inc-a", 1, [queuedBatch]));
      if (reads === 2) return new Promise<WebMessageQueueSnapshot>((resolve) => {
        resolvePreflight = resolve;
      });
      return Promise.resolve(queueSnapshot("scope-a", "inc-a", 2, [queuedBatch]));
    }),
    sendWebMessageQueueBatchNow: vi.fn(async () => {
      sends += 1;
      return { accepted: true };
    }),
  } as unknown as EngineApi;
  const controller = new WebQueuePanelController(api, (() => 1) as unknown as typeof globalThis.setInterval);
  controller.setIdentity("session-1", "conversation-a");
  await new Promise((resolve) => setTimeout(resolve, 0));

  const sending = controller.sendNow("batch-1");
  await Promise.resolve();
  await controller.refresh(true);
  resolvePreflight(queueSnapshot("scope-a", "inc-a", 2, [queuedBatch]));

  await expect(sending).resolves.toBeUndefined();
  expect(sends).toBe(1);
});

test("failed background refresh marks the visible queue stale until a fresh snapshot arrives", async () => {
  let fail = false;
  let revision = 1;
  const sendBatch = vi.fn(async () => ({ accepted: true }));
  const api = {
    getWebMessageQueue: vi.fn(async () => {
      if (fail) throw new Error("offline");
      return queueSnapshot("scope-a", "inc-a", revision, [queuedBatch]);
    }),
    sendWebMessageQueueBatchNow: sendBatch,
  } as unknown as EngineApi;
  const controller = new WebQueuePanelController(api, (() => 1) as unknown as typeof globalThis.setInterval);
  controller.setIdentity("session-1", "conversation-a");
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(controller.getState().snapshot?.revision).toBe(1);

  fail = true;
  await controller.refresh(true);
  expect(controller.getState().snapshot?.revision).toBe(1);
  expect(controller.getState().stale).toBe(true);
  expect(controller.getState().refreshError).toBe(true);

  fail = false;
  revision = 0;
  await expect(controller.sendNow("batch-1")).rejects.toThrow(/status is out of date/);
  expect(sendBatch).not.toHaveBeenCalled();

  revision = 2;
  await controller.refresh(true);
  expect(controller.getState().snapshot?.revision).toBe(2);
  expect(controller.getState().stale).toBe(false);
  expect(controller.getState().refreshError).toBe(false);
});

function busyInput(overrides: Partial<BusyWebMessageInput> = {}): BusyWebMessageInput {
  return {
    sessionId: "session-a",
    conversationUid: "conversation-a",
    draftKey: "draft-a",
    text: "inspect this",
    attachments: [],
    sendNow: false,
    ...overrides,
  };
}

function busyApiHarness() {
  const allocateWebMessageQueueIds = vi.fn(async (_sessionId: string, _uid: string, _items: number, _ops: number, attachmentCount: number) => ({
    scope_id: "scope-a",
    incarnation: "inc-a",
    operation_ids: ["operation-a"],
    batch_id: "batch-a",
    request_ids: ["request-a"],
    attachment_operation_ids: Array.from({ length: attachmentCount }, (_, i) => `attachment-op-${i}`),
  }));
  const stageWebMessageQueueAttachment = vi.fn(async (_sessionId: string, _uid: string, operationId: string, image: Blob) => ({
    ref: `ref-${operationId}`,
    mime: image.type,
    size: image.size,
    filename: image instanceof File ? image.name : "Image",
  }));
  const enqueueWebMessageBatch = vi.fn(async (_sessionId: string, body: { operation_id: string; batch_id: string; items: Array<{ request_id: string }> }) => ({
    scope_id: "scope-a",
    incarnation: "inc-a",
    operation_id: body.operation_id,
    revision: 1,
    status: "queued",
    batch_id: body.batch_id,
    request_ids: body.items.map((item) => item.request_id),
  }));
  const sendWebMessageQueueBatchNow = vi.fn(async () => ({ accepted: false, status: "busy" }));
  return {
    api: { allocateWebMessageQueueIds, stageWebMessageQueueAttachment, enqueueWebMessageBatch, sendWebMessageQueueBatchNow } as unknown as EngineApi,
    allocateWebMessageQueueIds,
    stageWebMessageQueueAttachment,
    enqueueWebMessageBatch,
    sendWebMessageQueueBatchNow,
  };
}

test("busy composer admission stages images before enqueue and retains accepted Send Now batches", async () => {
  const storage = new MemoryStorage();
  const locks = new SerialQueueLocks();
  const { api, allocateWebMessageQueueIds, stageWebMessageQueueAttachment, enqueueWebMessageBatch, sendWebMessageQueueBatchNow } = busyApiHarness();
  const imageBytes = Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 0]);
  const attachment: BusyWebMessageAttachment = {
    id: "image-1",
    type: "image",
    name: "diagram.png",
    contentType: "image/png",
    file: new File([imageBytes], "diagram.png", { type: "image/png" }),
  };

  const result = await submitBusyWebMessage(api, busyInput({ attachments: [attachment], sendNow: true }), storage, locks);

  expect(allocateWebMessageQueueIds).toHaveBeenCalledWith("session-a", "conversation-a", 1, 1, 1);
  expect(stageWebMessageQueueAttachment.mock.calls.map((call) => call[2])).toEqual(["attachment-op-0"]);
  expect(enqueueWebMessageBatch.mock.calls[0]?.[1]).toMatchObject({
    conversation_uid: "conversation-a",
    operation_id: "operation-a",
    batch_id: "batch-a",
    items: [{ request_id: "request-a", text: "inspect this", attachment_refs: ["ref-attachment-op-0"] }],
  });
  expect(sendWebMessageQueueBatchNow).toHaveBeenCalledWith("session-a", "conversation-a", "batch-a");
  expect(result).toMatchObject({ admitted: true, sendNowStatus: "not-accepted", batchId: "batch-a" });
  expect(storage.values.size).toBe(1);
  const persistedPlan = JSON.stringify([...storage.values.values()]);
  expect(persistedPlan).not.toContain("inspect this");
  expect(persistedPlan).not.toContain("diagram.png");
});

test("busy composer retries an ambiguous enqueue with the same daemon IDs and blocks changed content", async () => {
  const storage = new MemoryStorage();
  const locks = new SerialQueueLocks();
  const { api, allocateWebMessageQueueIds, enqueueWebMessageBatch } = busyApiHarness();
  enqueueWebMessageBatch.mockRejectedValueOnce(new Error("connection closed after admission"));

  await expect(submitBusyWebMessage(api, busyInput(), storage, locks)).rejects.toThrow(/confirm queue admission/);
  expect(hasUnresolvedBusyWebMessage("session-a", "draft-a", storage)).toBe(true);
  const result = await submitBusyWebMessage(api, busyInput(), storage, locks);
  expect(hasUnresolvedBusyWebMessage("session-a", "draft-a", storage)).toBe(false);
  expect(result.admitted).toBe(true);
  expect(allocateWebMessageQueueIds).toHaveBeenCalledOnce();
  expect(enqueueWebMessageBatch.mock.calls.map((call) => call[1].operation_id)).toEqual([
    "operation-a",
    "operation-a",
  ]);

  const otherStorage = new MemoryStorage();
  enqueueWebMessageBatch.mockRejectedValueOnce(new Error("response lost"));
  await expect(submitBusyWebMessage(api, busyInput({ draftKey: "draft-b" }), otherStorage, locks)).rejects.toThrow(/confirm queue admission/);
  await expect(submitBusyWebMessage(api, busyInput({ draftKey: "draft-b", conversationUid: "conversation-b" }), otherStorage, locks))
    .rejects.toThrow(/previous queue submission/);
  await expect(submitBusyWebMessage(api, busyInput({ draftKey: "draft-b", text: "changed" }), otherStorage, locks))
    .rejects.toThrow(/previous queue submission/);
  expect(allocateWebMessageQueueIds).toHaveBeenCalledTimes(2);
});

test("lost stage response retries the same staging operation instead of orphaning a second blob", async () => {
  const storage = new MemoryStorage();
  const locks = new SerialQueueLocks();
  let allocation = 0;
  const allocateWebMessageQueueIds = vi.fn(async (_s: string, _uid: string, _items: number, _ops: number, count: number) => {
    allocation += 1;
    return {
      scope_id: "scope-a", incarnation: "inc-a", operation_ids: [`enqueue-${allocation}`],
      batch_id: `batch-${allocation}`, request_ids: [`request-${allocation}`],
      attachment_operation_ids: Array.from({ length: count }, (_, index) => `stage-${allocation}-${index}`),
    };
  });
  const stageOperationIds: string[] = [];
  const stagedByOperation = new Map<string, { ref: string; mime: string; size: number; filename: string }>();
  let loseFirstStageResponse = true;
  const stageWebMessageQueueAttachment = vi.fn(async (_s: string, _uid: string, operationId: string, file: Blob) => {
    stageOperationIds.push(operationId);
    let staged = stagedByOperation.get(operationId);
    if (!staged) {
      staged = {
        ref: `ref-${stagedByOperation.size + 1}`,
        mime: file.type,
        size: file.size,
        filename: file instanceof File ? file.name : "Image",
      };
      stagedByOperation.set(operationId, staged);
    }
    if (loseFirstStageResponse) {
      loseFirstStageResponse = false;
      throw new TypeError("network response lost after daemon stored attachment");
    }
    return staged;
  });
  const enqueueWebMessageBatch = vi.fn(async (_s: string, body: {
    operation_id: string;
    batch_id: string;
    items: Array<{ request_id: string; attachment_refs: string[] }>;
  }) => ({
    scope_id: "scope-a", incarnation: "inc-a", operation_id: body.operation_id,
    revision: 1, status: "queued", batch_id: body.batch_id,
    request_ids: body.items.map((item) => item.request_id),
  }));
  const api = { allocateWebMessageQueueIds, stageWebMessageQueueAttachment, enqueueWebMessageBatch } as unknown as EngineApi;
  const bytes = Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10, 0]);
  const input = busyInput({ attachments: [{
    id: "image-a", type: "image", name: "image.png", contentType: "image/png",
    file: new File([bytes], "image.png", { type: "image/png" }),
  }] });

  await expect(submitBusyWebMessage(api, input, storage, locks))
    .rejects.toBeInstanceOf(QueueSubmissionReconciliationError);
  expect(hasUnresolvedBusyWebMessage("session-a", "draft-a", storage)).toBe(true);
  const retry = await submitBusyWebMessage(api, input, storage, locks);

  expect(retry.admitted).toBe(true);
  expect(allocateWebMessageQueueIds).toHaveBeenCalledOnce();
  expect(stageOperationIds).toEqual(["stage-1-0", "stage-1-0"]);
  expect(stagedByOperation.size).toBe(1);
  expect(enqueueWebMessageBatch.mock.calls[0]?.[1].items[0]?.attachment_refs).toEqual(["ref-1"]);
});

test("an ambiguous plan for a prior conversation stays scoped there while a new conversation queues independently", async () => {
  const storage = new MemoryStorage();
  const locks = new SerialQueueLocks();
  let allocation = 0;
  const allocateWebMessageQueueIds = vi.fn(async (_s: string, uid: string) => {
    allocation += 1;
    return {
      scope_id: `scope-${uid}`, incarnation: `inc-${uid}`, operation_ids: [`operation-${allocation}`],
      batch_id: `batch-${allocation}`, request_ids: [`request-${allocation}`],
      attachment_operation_ids: [],
    };
  });
  const admissions: Array<{ uid: string; operationId: string; requestId: string }> = [];
  let loseConversationAResponse = true;
  const enqueueWebMessageBatch = vi.fn(async (_s: string, body: {
    conversation_uid: string;
    operation_id: string;
    batch_id: string;
    items: Array<{ request_id: string }>;
  }) => {
    admissions.push({ uid: body.conversation_uid, operationId: body.operation_id, requestId: body.items[0]!.request_id });
    if (body.conversation_uid === "conversation-a" && loseConversationAResponse) {
      loseConversationAResponse = false;
      throw new TypeError("response lost after admission");
    }
    return {
      scope_id: `scope-${body.conversation_uid}`, incarnation: `inc-${body.conversation_uid}`,
      operation_id: body.operation_id, revision: 1, status: "queued", batch_id: body.batch_id,
      request_ids: body.items.map((item) => item.request_id),
    };
  });
  const api = { allocateWebMessageQueueIds, enqueueWebMessageBatch } as unknown as EngineApi;

  await expect(submitBusyWebMessage(api, busyInput(), storage, locks))
    .rejects.toBeInstanceOf(QueueSubmissionReconciliationError);
  const conversationB = await submitBusyWebMessage(api, busyInput({
    conversationUid: "conversation-b", text: "message for B",
  }), storage, locks);
  const conversationARetry = await submitBusyWebMessage(api, busyInput(), storage, locks);

  expect(conversationB.requestId).toBe("request-2");
  expect(conversationARetry.requestId).toBe("request-1");
  expect(allocateWebMessageQueueIds).toHaveBeenCalledTimes(2);
  expect(admissions).toEqual([
    { uid: "conversation-a", operationId: "operation-1", requestId: "request-1" },
    { uid: "conversation-b", operationId: "operation-2", requestId: "request-2" },
    { uid: "conversation-a", operationId: "operation-1", requestId: "request-1" },
  ]);
  expect([...storage.values.values()].map((raw) => JSON.parse(raw).conversation_uid).sort())
    .toEqual(["conversation-a", "conversation-b"]);
});

test("a late acknowledgement cannot erase a newer ambiguous submission in another tab", async () => {
  const storage = new MemoryStorage();
  const locks = new SerialQueueLocks();
  let allocation = 0;
  const allocate = vi.fn(async (_s: string, _u: string, _i: number, _o: number, _a: number) => {
    allocation += 1;
    return {
      scope_id: "scope-a", incarnation: "inc-a", operation_ids: [`operation-${allocation}`],
      batch_id: `batch-${allocation}`, request_ids: [`request-${allocation}`],
      attachment_operation_ids: [],
    };
  });
  const secondEnqueueStarted = deferred<void>();
  const finishSecondEnqueue = deferred<void>();
  const admittedOperations = new Set<string>();
  let secondWasAmbiguous = false;
  const enqueue = vi.fn(async (_s: string, body: { operation_id: string; batch_id: string; items: Array<{ request_id: string }> }) => {
    admittedOperations.add(body.operation_id);
    if (body.operation_id === "operation-2" && !secondWasAmbiguous) {
      secondWasAmbiguous = true;
      secondEnqueueStarted.resolve();
      await finishSecondEnqueue.promise;
      throw new Error("response lost after commit");
    }
    return {
      scope_id: "scope-a", incarnation: "inc-a", operation_id: body.operation_id,
      revision: 1, status: "queued", batch_id: body.batch_id,
      request_ids: body.items.map((item) => item.request_id),
    };
  });
  const api = { allocateWebMessageQueueIds: allocate, enqueueWebMessageBatch: enqueue } as unknown as EngineApi;
  const first = await submitBusyWebMessage(api, busyInput({ text: "first" }), storage, locks);

  const secondSubmit = submitBusyWebMessage(api, busyInput({ text: "second" }), storage, locks);
  await secondEnqueueStarted.promise;
  const firstAck = acknowledgeBusyWebMessage(first, storage, locks);
  finishSecondEnqueue.resolve();
  await expect(secondSubmit).rejects.toBeInstanceOf(QueueSubmissionReconciliationError);
  await expect(firstAck).resolves.toBe(true);

  const retried = await submitBusyWebMessage(api, busyInput({ text: "second" }), storage, locks);
  expect(retried.requestId).toBe("request-2");
  expect(allocate).toHaveBeenCalledTimes(2);
  expect(enqueue.mock.calls.map((call) => call[1].operation_id)).toEqual([
    "operation-1", "operation-2", "operation-2",
  ]);
  expect(admittedOperations).toEqual(new Set(["operation-1", "operation-2"]));
});

test("Web Locks denial fails closed before allocating queue identities", async () => {
  const { api, allocateWebMessageQueueIds } = busyApiHarness();
  const denied: QueueSubmissionLocks = {
    request: async () => { throw new Error("NotAllowedError"); },
  };
  await expect(submitBusyWebMessage(api, busyInput(), new MemoryStorage(), denied))
    .rejects.toThrow(/cross-tab queue lock/);
  expect(allocateWebMessageQueueIds).not.toHaveBeenCalled();
});

test("missing Web Locks fails closed instead of using unsynchronized localStorage", async () => {
  vi.stubGlobal("navigator", {});
  const { api, allocateWebMessageQueueIds } = busyApiHarness();
  await expect(submitBusyWebMessage(api, busyInput(), new MemoryStorage()))
    .rejects.toThrow(/cannot safely coordinate queue retries across tabs/);
  expect(allocateWebMessageQueueIds).not.toHaveBeenCalled();
});

test("client rejects image batches above the daemon 20 MiB per-message cap before staging", async () => {
  const { api, allocateWebMessageQueueIds, stageWebMessageQueueAttachment } = busyApiHarness();
  const images = [0, 1, 2].map((index) => {
    const bytes = new Uint8Array(7 * 1024 * 1024);
    bytes.set([137, 80, 78, 71, 13, 10, 26, 10]);
    return {
      id: `image-${index}`, type: "image", name: `image-${index}.png`,
      contentType: "image/png", file: new File([bytes], `image-${index}.png`, { type: "image/png" }),
    } as BusyWebMessageAttachment;
  });
  await expect(submitBusyWebMessage(
    api, busyInput({ attachments: images }), new MemoryStorage(), new SerialQueueLocks(),
  )).rejects.toThrow(/20 MiB per-message limit/);
  expect(allocateWebMessageQueueIds).not.toHaveBeenCalled();
  expect(stageWebMessageQueueAttachment).not.toHaveBeenCalled();
});

test("a definite image-stage rejection releases its plan so corrected content can be admitted", async () => {
  const storage = new MemoryStorage();
  const locks = new SerialQueueLocks();
  let allocation = 0;
  const allocate = vi.fn(async (_s: string, _u: string, _i: number, _o: number, count: number) => {
    allocation += 1;
    return {
      scope_id: "scope-a", incarnation: "inc-a", operation_ids: [`operation-${allocation}`],
      batch_id: `batch-${allocation}`, request_ids: [`request-${allocation}`],
      attachment_operation_ids: Array.from({ length: count }, (_, index) => `attachment-${allocation}-${index}`),
    };
  });
  const stage = vi.fn(async () => { throw new ApiError("unsupported image", 415, {}); });
  const enqueue = vi.fn(async (_s: string, body: { operation_id: string; batch_id: string; items: Array<{ request_id: string }> }) => ({
    scope_id: "scope-a", incarnation: "inc-a", operation_id: body.operation_id,
    revision: 1, status: "queued", batch_id: body.batch_id,
    request_ids: body.items.map((item) => item.request_id),
  }));
  const api = { allocateWebMessageQueueIds: allocate, stageWebMessageQueueAttachment: stage, enqueueWebMessageBatch: enqueue } as unknown as EngineApi;
  const image = new File([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])], "bad.png", { type: "image/png" });
  const rejectedInput = busyInput({ attachments: [{
    id: "bad", type: "image", name: image.name, contentType: image.type, file: image,
  }] });

  await expect(submitBusyWebMessage(api, rejectedInput, storage, locks))
    .rejects.toThrow(/rejected this attachment before queue admission/);
  expect(hasUnresolvedBusyWebMessage("session-a", "draft-a", storage)).toBe(false);
  const accepted = await submitBusyWebMessage(api, busyInput({ text: "corrected text" }), storage, locks);
  expect(accepted.requestId).toBe("request-2");
  expect(allocate).toHaveBeenCalledTimes(2);
  expect(enqueue).toHaveBeenCalledOnce();
});

test("a definite enqueue rejection releases its exact plan for a corrected retry", async () => {
  const storage = new MemoryStorage();
  const locks = new SerialQueueLocks();
  let allocation = 0;
  const allocate = vi.fn(async () => {
    allocation += 1;
    return {
      scope_id: "scope-a", incarnation: "inc-a", operation_ids: [`operation-${allocation}`],
      batch_id: `batch-${allocation}`, request_ids: [`request-${allocation}`], attachment_operation_ids: [],
    };
  });
  const enqueue = vi.fn(async (_s: string, body: { operation_id: string; batch_id: string; items: Array<{ request_id: string }> }) => {
    if (body.operation_id === "operation-1") throw new ApiError("payload too large", 413, {});
    return {
      scope_id: "scope-a", incarnation: "inc-a", operation_id: body.operation_id,
      revision: 1, status: "queued", batch_id: body.batch_id,
      request_ids: body.items.map((item) => item.request_id),
    };
  });
  const api = { allocateWebMessageQueueIds: allocate, enqueueWebMessageBatch: enqueue } as unknown as EngineApi;
  await expect(submitBusyWebMessage(api, busyInput(), storage, locks))
    .rejects.toThrow(/rejected this message before queue admission/);
  const corrected = await submitBusyWebMessage(api, busyInput({ text: "shorter" }), storage, locks);
  expect(corrected.requestId).toBe("request-2");
  expect(allocate).toHaveBeenCalledTimes(2);
});

test("typed daemon queue-capacity rejection releases the draft plan; unrelated 409 stays ambiguous", async () => {
  const storage = new MemoryStorage();
  const locks = new SerialQueueLocks();
  let allocation = 0;
  const allocate = vi.fn(async () => {
    allocation += 1;
    return {
      scope_id: "scope-a", incarnation: "inc-a", operation_ids: [`operation-${allocation}`],
      batch_id: `batch-${allocation}`, request_ids: [`request-${allocation}`], attachment_operation_ids: [],
    };
  });
  let rejectCapacity = true;
  const enqueue = vi.fn(async (_s: string, body: { operation_id: string; batch_id: string; items: Array<{ request_id: string }> }) => {
    if (rejectCapacity) {
      rejectCapacity = false;
      throw new ApiError("queue full", 409, {
        detail: { code: "queue_capacity", message: "The queue is full. No message was admitted." },
      });
    }
    return {
      scope_id: "scope-a", incarnation: "inc-a", operation_id: body.operation_id,
      revision: 1, status: "queued", batch_id: body.batch_id,
      request_ids: body.items.map((item) => item.request_id),
    };
  });
  const api = { allocateWebMessageQueueIds: allocate, enqueueWebMessageBatch: enqueue } as unknown as EngineApi;
  await expect(submitBusyWebMessage(api, busyInput(), storage, locks))
    .rejects.toThrow(/queue is full/);
  expect(hasUnresolvedBusyWebMessage("session-a", "draft-a", storage)).toBe(false);
  const corrected = await submitBusyWebMessage(api, busyInput({ text: "after capacity clears" }), storage, locks);
  expect(corrected.requestId).toBe("request-2");
  expect(allocate).toHaveBeenCalledTimes(2);

  const conflictStorage = new MemoryStorage();
  const conflictLocks = new SerialQueueLocks();
  const { api: conflictApi, enqueueWebMessageBatch: conflictEnqueue } = busyApiHarness();
  conflictEnqueue.mockRejectedValueOnce(new ApiError("generic conflict", 409, { detail: "conflict" }));
  await expect(submitBusyWebMessage(conflictApi, busyInput(), conflictStorage, conflictLocks))
    .rejects.toBeInstanceOf(QueueSubmissionReconciliationError);
  await expect(submitBusyWebMessage(conflictApi, busyInput({ text: "different" }), conflictStorage, conflictLocks))
    .rejects.toBeInstanceOf(QueueSubmissionReconciliationError);
});

test("ambiguous queue handler conflict remains retryable with the same operation ID", async () => {
  const storage = new MemoryStorage();
  const locks = new SerialQueueLocks();
  const { api, allocateWebMessageQueueIds, enqueueWebMessageBatch } = busyApiHarness();
  enqueueWebMessageBatch.mockRejectedValueOnce(new ApiError("The daemon rejected this queue operation.", 409, {}));

  await expect(submitBusyWebMessage(api, busyInput(), storage, locks))
    .rejects.toBeInstanceOf(QueueSubmissionReconciliationError);
  const retried = await submitBusyWebMessage(api, busyInput(), storage, locks);

  expect(retried.admitted).toBe(true);
  expect(allocateWebMessageQueueIds).toHaveBeenCalledOnce();
  expect(enqueueWebMessageBatch.mock.calls.map((call) => call[1].operation_id)).toEqual([
    "operation-a", "operation-a",
  ]);
});

test("busy composer uses authenticated EngineApi routes for ID allocation, image staging, enqueue, and Send Now", async () => {
  const requests: Array<{ url: string; method: string; authorization: string | null; contentType: string | null; body: unknown }> = [];
  const bytes = Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 0]);
  vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/config") return Response.json({});
    const method = init?.method ?? "GET";
    const headers = new Headers(init?.headers);
    requests.push({
      url,
      method,
      authorization: headers.get("Authorization"),
      contentType: headers.get("Content-Type"),
      body: init?.body,
    });
    if (url.endsWith("/web-message-queue/ids")) {
      return Response.json({
        scope_id: "scope-http",
        incarnation: "inc-http",
        operation_ids: ["op-http"],
        batch_id: "batch-http",
        request_ids: ["request-http"],
        attachment_operation_ids: ["att-op-http"],
      });
    }
    if (method === "PUT" && url.includes("/attachments/")) {
      return Response.json({ ref: "ref-http", mime: "image/png", size: bytes.length, filename: "diagram.png" });
    }
    if (url.endsWith("/web-message-queue/batches")) {
      const body = JSON.parse(String(init?.body));
      return Response.json({
        scope_id: "scope-http",
        incarnation: "inc-http",
        operation_id: body.operation_id,
        revision: 1,
        status: "queued",
        batch_id: body.batch_id,
        request_ids: body.items.map((item: { request_id: string }) => item.request_id),
      });
    }
    if (url.includes("/send-now?")) return Response.json({ accepted: true, status: "admitted" });
    throw new Error(`Unexpected request ${method} ${url}`);
  });

  const api = new EngineApi("http://engine");
  api.setToken("queue-auth-token");
  const attachment: BusyWebMessageAttachment = {
    id: "image-http",
    type: "image",
    name: "diagram.png",
    contentType: "image/png",
    file: new File([bytes], "diagram.png", { type: "image/png" }),
  };
  const result = await submitBusyWebMessage(api, busyInput({ attachments: [attachment], sendNow: true }), new MemoryStorage(), new SerialQueueLocks());

  expect(result).toMatchObject({ admitted: true, sendNowStatus: "accepted", batchId: "batch-http" });
  expect(requests.map(({ method, url }) => [method, url.split("http://engine")[1]])).toEqual([
    ["POST", "/sessions/session-a/web-message-queue/ids"],
    ["PUT", "/sessions/session-a/web-message-queue/attachments/att-op-http?conversation_uid=conversation-a"],
    ["POST", "/sessions/session-a/web-message-queue/batches"],
    ["POST", "/sessions/session-a/web-message-queue/batches/batch-http/send-now?conversation_uid=conversation-a"],
  ]);
  expect(requests.every((request) => request.authorization === "Bearer queue-auth-token")).toBe(true);
  expect(requests[1]?.contentType).toBe("image/png");
  expect(requests[1]?.body).toBeInstanceOf(Blob);
  expect(JSON.parse(String(requests[2]?.body))).toMatchObject({
    conversation_uid: "conversation-a",
    operation_id: "op-http",
    batch_id: "batch-http",
    items: [{ request_id: "request-http", attachment_refs: ["ref-http"] }],
  });
});
