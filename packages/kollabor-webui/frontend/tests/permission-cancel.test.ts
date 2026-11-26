import assert from "node:assert/strict";
import test from "node:test";
import { ApiError, EngineApi } from "../src/api.ts";
import type { ChatPart, ChatRow } from "../src/chat-stream.ts";
import { cancelPendingPermission, pendingPermissions, permissionOwner } from "../src/utils/permission-cancel.ts";

const owner = (toolId: string, requestId = toolId, source = "acp", parked = false) => ({ toolId, requestId, source, parked });
const row = (id: string, parts: ChatPart[], status = "running"): ChatRow => ({
  id, seq: 1, kind: "tool", role: "assistant", turn_id: "turn-1", status, parts, created_at: "2026-11-25T00:00:00Z",
});
const call = (toolCallId: string, toolName: string, args: unknown, result?: unknown): ChatPart => ({
  type: "tool-call", tool_call_id: toolCallId, tool_name: toolName, args, result,
});
const permission = (id: string, result?: unknown) =>
  call(`call-${id}`, "request_permission", { tool_id: id, request_id: `turn-${id}`, details: { source: "acp", parked: false } }, result);

test("pendingPermissions returns only request_permission tool-call parts with no result", () => {
  assert.deepEqual(pendingPermissions([
    row("pending", [permission("pending")]),
    row("native-without-owner", [call("call-native", "request_permission", { tool_id: "native-without-owner" })]),
    row("other-tool", [call("call-read", "read_file", { tool_id: "other" })]),
    row("text", [{ type: "text", text: "no tool call" }]),
  ]), [owner("pending", "turn-pending")]);
  assert.equal(permissionOwner({ tool_id: "" }), null);
});

test("a permission part with a result is not pending", () => {
  assert.deepEqual(pendingPermissions([
    row("answered", [permission("answered", { decision: "deny" })], "complete"),
    row("pending", [permission("pending")]),
  ]), [owner("pending", "turn-pending")]);
});

test("a result preview completes a permission call while a null optional preview stays pending", () => {
  const resultPreview = {
    kind: "chat-stream-tool-output-preview" as const,
    session_id: "session-1",
    row_id: "external-result",
    tool_call_id: "call-external-result",
    output_bytes: 80_000,
    output_chars: 80_000,
    output_type: "text" as const,
    output_revision: "a".repeat(64),
    preview: "finished",
  };
  assert.deepEqual(pendingPermissions([
    row("external-result", [{ ...permission("external-result"), chat_stream_result_preview: resultPreview }]),
    row("null-preview", [{ ...permission("null-preview"), chat_stream_result_preview: null }]),
    row("null-result", [permission("null-result", null)]),
  ]), [owner("null-preview", "turn-null-preview")]);
});

test("live and restored permission args read the top-level turn owner, not nested detail metadata", () => {
  const liveArgs = {
    tool_id: "reused-tool", request_id: "parent-live", permission_id: "owner-live",
    details: { source: "acp", request_id: "vendor-live", parked: false },
  };
  const restoredArgs = {
    tool_id: "reused-tool", request_id: "parent-restored", permission_id: "owner-restored",
    details: { source: "acp", request_id: "vendor-restored", parked: true },
  };
  assert.deepEqual(permissionOwner(liveArgs), owner("reused-tool", "parent-live", "acp", false));
  assert.deepEqual(permissionOwner(restoredArgs), owner("reused-tool", "parent-restored", "acp", true));
  assert.deepEqual(pendingPermissions([
    row("live", [call("call-live", "request_permission", liveArgs)]),
    row("restored", [call("call-restored", "request_permission", restoredArgs)]),
  ]), [owner("reused-tool", "parent-live", "acp", false), owner("reused-tool", "parent-restored", "acp", true)]);
  assert.equal(permissionOwner({
    tool_id: "old-prompt", details: { source: "acp", request_id: "vendor-only" },
  })?.requestId, undefined, "nested provider metadata is not the parent request owner");
});

test("a parked prompt fails closed for request-scoped Stop", async () => {
  const parked = [owner("same-tool", "old-turn", "acp", true)];
  let reads = 0, cancellations = 0;
  assert.equal(await cancelPendingPermission({
    expected: parked,
    current: () => parked,
    readPending: async () => { reads++; return parked; },
    cancel: async () => { cancellations++; return { cancelled: true }; },
  }), false);
  assert.equal(reads, 0);
  assert.equal(cancellations, 0);
});

test("a stale click after resolution never fetches or cancels a replacement turn", async () => {
  const rendered = [owner("old")];
  for (const current of [
    [],
    [owner("new")],
    [owner("old", "replacement-parent")],
    [owner("old", "old", "native")],
  ]) {
    let reads = 0, cancellations = 0;
    const cancelled = await cancelPendingPermission({
      expected: rendered, current: () => current,
      readPending: async () => { reads++; return current; },
      cancel: async () => { cancellations++; return {}; },
    });
    assert.equal(cancelled, false);
    assert.equal(reads, 0);
    assert.equal(cancellations, 0);
  }
});

test("resolution or a different daemon owner prevents cancellation, even with a reused tool ID", async () => {
  for (const daemon of [
    [],
    [owner("replacement")],
    [owner("old", "new-turn")],
    [owner("old", "old", "native")],
  ]) {
    let cancellations = 0;
    assert.equal(await cancelPendingPermission({
      expected: [owner("old")], current: () => [owner("old")], readPending: async () => daemon,
      cancel: async () => { cancellations++; return {}; },
    }), false);
    assert.equal(cancellations, 0);
  }
});

test("resolution or session unmount during the read cannot cancel new work", async () => {
  for (const replacement of [[], [owner("old", "new-session-turn")]]) {
    let current = [owner("old")], cancellations = 0;
    assert.equal(await cancelPendingPermission({
      expected: [owner("old")], current: () => current,
      readPending: async () => { current = replacement; return [owner("old")]; },
      cancel: async () => { cancellations++; return {}; },
    }), false);
    assert.equal(cancellations, 0);
  }
});

test("a live, reloaded, or external-client ACP prompt passes its parent ID rather than its tool ID", async () => {
  const sent: Array<string | undefined> = [];
  assert.equal(await cancelPendingPermission({
    expected: [owner("opaque-tool", "parent-request")], current: () => [owner("opaque-tool", "parent-request")],
    readPending: async () => [owner("opaque-tool", "parent-request")],
    cancel: async (requestId) => { sent.push(requestId); return { cancelled: true }; },
  }), true);
  assert.deepEqual(sent, ["parent-request"]);
});

test("the global Stop control does not guess when distinct pending owners coexist", async () => {
  const siblings = [owner("reused-tool", "parent-A"), owner("reused-tool", "parent-B")];
  let reads = 0, cancellations = 0;
  assert.equal(await cancelPendingPermission({
    expected: siblings,
    current: () => siblings,
    readPending: async () => { reads++; return siblings; },
    cancel: async () => { cancellations++; return { cancelled: true }; },
  }), false);
  assert.equal(reads, 0);
  assert.equal(cancellations, 0);
});

test("a selected sibling cancellation stays on that exact request owner", async () => {
  const siblings = [owner("reused-tool", "parent-A"), owner("reused-tool", "parent-B")];
  const sent: string[] = [];
  assert.equal(await cancelPendingPermission({
    expected: [siblings[1]],
    current: () => siblings,
    readPending: async () => siblings,
    cancel: async (requestId) => { sent.push(requestId); return { cancelled: true }; },
  }), true);
  assert.deepEqual(sent, ["parent-B"]);
});

test("an ACP permission missing a parent owner fails closed", async () => {
  const pending = { toolId: "opaque-tool", source: "acp" };
  let cancellations = 0;
  assert.equal(await cancelPendingPermission({
    expected: [pending], current: () => [pending], readPending: async () => [pending],
    cancel: async () => { cancellations++; return {}; },
  }), false);
  assert.equal(cancellations, 0);
});

test("native prompts without a parent cannot use permission-specific cancellation", async () => {
  const pending = { toolId: "native-tool" };
  const sent: string[] = [];
  assert.equal(await cancelPendingPermission({
    expected: [pending], current: () => [pending], readPending: async () => [pending],
    cancel: async (requestId) => { sent.push(requestId); return {}; },
  }), false);
  assert.deepEqual(sent, []);
});

test("the daemon's stale-owner result never falls back to unscoped cancellation", async () => {
  const sent: Array<string | undefined> = [];
  assert.equal(await cancelPendingPermission({
    expected: [owner("tool", "ended-turn")], current: () => [owner("tool", "ended-turn")],
    readPending: async () => [owner("tool", "ended-turn")],
    cancel: async (requestId) => { sent.push(requestId); return { cancelled: false }; },
  }), false);
  assert.deepEqual(sent, ["ended-turn"]);
});

test("a failed permission lookup cannot send cancellation", async () => {
  let cancellations = 0;
  await assert.rejects(cancelPendingPermission({
    expected: [owner("current")], current: () => [owner("current")],
    readPending: async () => { throw new Error("offline"); },
    cancel: async () => { cancellations++; return {}; },
  }), /offline/);
  assert.equal(cancellations, 0);
});

test("HTTP cancellation carries only an explicit parent ID; streaming cancellation keeps no body", async (t) => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  t.mock.method(globalThis, "fetch", async (url: string, init?: RequestInit) => {
    if (url === "/api/config") return Response.json({ engine_url: "http://127.0.0.1:9000" });
    requests.push({ url, init });
    return Response.json({ ok: true, cancelled: false });
  });
  const api = new EngineApi();
  assert.equal((await api.cancelOwned("session/one", "opaque-parent")).cancelled, false);
  await api.cancel("session/two");
  assert.equal(requests[0].url, "http://127.0.0.1:9000/sessions/session%2Fone/cancel-owned");
  assert.deepEqual(JSON.parse(String(requests[0].init?.body)), { request_id: "opaque-parent" });
  assert.equal(requests[1].init?.body, undefined);
  assert.equal(requests[1].url, "http://127.0.0.1:9000/sessions/session%2Ftwo/cancel");
});

test("an older engine's unsupported owner endpoint never becomes an unscoped cancel", async (t) => {
  const requests: string[] = [];
  t.mock.method(globalThis, "fetch", async (url: string) => {
    if (url === "/api/config") return Response.json({ engine_url: "http://127.0.0.1:9000" });
    requests.push(url);
    return Response.json({ detail: "Not Found" }, { status: 404 });
  });
  const api = new EngineApi();
  await assert.rejects(cancelPendingPermission({
    expected: [owner("tool", "parent")], current: () => [owner("tool", "parent")],
    readPending: async () => [owner("tool", "parent")],
    cancel: (requestId) => api.cancelOwned("existing-session", requestId!),
  }), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.message, "Not Found");
    assert.equal(error.status, 404);
    return true;
  });
  assert.deepEqual(requests, ["http://127.0.0.1:9000/sessions/existing-session/cancel-owned"]);
});
