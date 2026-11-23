import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import { ApiError, EngineApi } from "../src/api.ts";

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
    if (url.endsWith("/send-now")) {
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
