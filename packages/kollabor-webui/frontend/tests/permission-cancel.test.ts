import assert from "node:assert/strict";
import test from "node:test";
import { EngineApi } from "../src/api.ts";
import { cancelPendingPermission, pendingPermissions, permissionOwner, promptParkedSinceShown } from "../src/utils/permission-cancel.ts";

const owner = (toolId: string, requestId = toolId) => ({ toolId, requestId, source: "acp" });
const permission = (id: string, result?: unknown, status = "requires-action") => ({
  status: { type: status },
  content: [{ type: "tool-call", toolName: "request_permission", args: { tool_id: id, details: { source: "acp", request_id: `turn-${id}` } }, result }],
});

test("only unresolved permission parts expose their parent request owner", () => {
  assert.deepEqual(pendingPermissions([
    permission("pending"), permission("answered", { decision: "deny" }),
    permission("ended", undefined, "incomplete"),
    { status: { type: "requires-action" }, content: [{ type: "tool-call", toolName: "request_permission", args: { tool_id: "native-without-owner" } }] },
    { status: { type: "requires-action" }, content: [{ type: "tool-call", toolName: "read_file", args: { tool_id: "other" } }] },
  ]), [owner("pending", "turn-pending")]);
  assert.equal(permissionOwner({ tool_id: "" }), null);
});

test("a stale click after resolution never fetches or cancels a replacement turn", async () => {
  const rendered = [owner("old")];
  for (const current of [[], [owner("new")], [owner("old", "replacement-parent")]]) {
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
  for (const daemon of [[], [owner("replacement")], [owner("old", "new-turn")]]) {
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
  }), /404/);
  assert.deepEqual(requests, ["http://127.0.0.1:9000/sessions/existing-session/cancel-owned"]);
});

test("a prompt its turn left open reloads a thread whose run showed it, once", () => {
  const card = (parked: boolean, result?: unknown) => ({
    type: "tool-call", toolName: "request_permission", result,
    args: { tool_id: "t1", details: parked ? { parked: true } : {} },
  });
  const waitingOn = (id: string, part: object) => ({ id, status: { type: "requires-action" }, content: [part] });
  const shownByRun = [{ id: "u", content: "go" }, waitingOn("assistant-s-1", card(false))];
  const reloadedParked = [{ id: "history-0", content: "go" }, waitingOn("permission-t1-0", card(true))];

  assert.equal(promptParkedSinceShown(shownByRun, reloadedParked), true);
  // After the whole reload the thread shows the loaded prompt: no second reload.
  assert.equal(promptParkedSinceShown(reloadedParked, reloadedParked), false);
  // A card that still waits on its turn, or one answered, is not parked.
  assert.equal(promptParkedSinceShown(shownByRun, [waitingOn("permission-t1-0", card(false))]), false);
  assert.equal(promptParkedSinceShown(shownByRun, [waitingOn("permission-t1-0", card(true, { decision: "deny" }))]), false);
  // A thread that waits on nothing is the waiting-mismatch rule's case.
  assert.equal(promptParkedSinceShown([{ id: "a", status: { type: "complete" }, content: [] }], reloadedParked), false);
});
