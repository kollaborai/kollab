import { afterEach, describe, expect, it, vi } from "vitest";
import { EngineApi } from "../src/api.ts";
import type { ChatRow } from "../src/chat-stream.ts";
import { cancelPendingPermission, respondToPermission } from "../src/permission-respond.ts";

const BASE = "http://127.0.0.1:9000";
const promptArgs = {
  tool_id: "shell-1", request_id: "parent-1", permission_id: "perm-1", details: { source: "acp", parked: false },
};
const openRow = (status = "requires-action"): ChatRow => ({
  id: "row-1", seq: 3, kind: "tool", role: "assistant", turn_id: "turn-1", status,
  parts: [{ type: "tool-call", tool_call_id: "call-1", tool_name: "request_permission", args: promptArgs }],
  created_at: "2026-11-25T00:00:00Z",
});
const answeredRow: ChatRow = {
  ...openRow("complete"),
  parts: [{
    type: "tool-call", tool_call_id: "call-1", tool_name: "request_permission", args: promptArgs,
    result: { decision: "approve" },
  }],
};
const ownerOfPrompt = { toolId: "shell-1", requestId: "parent-1", source: "acp", parked: false };

type Sent = { method: string; url: string; body: unknown };

/** A stub engine: every call gets `pending` back as the daemon's pending prompts. */
function engine(pending: readonly unknown[]) {
  const sent: Sent[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url === "/api/config") return Response.json({ engine_url: BASE });
    sent.push({ method: init?.method ?? "GET", url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return Response.json({ ok: true, cancelled: true, pending_prompts: pending });
  });
  return { api: new EngineApi(), sent };
}

const posts = (sent: Sent[]) => sent.filter((request) => request.method === "POST");

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("respondToPermission", () => {
  it("posts the full body to /sessions/{id}/permission and returns 'answered' on success", async () => {
    const { api, sent } = engine([promptArgs]);
    const outcome = await respondToPermission({
      api, sessionId: "session/one", toolCallId: "call-1", args: promptArgs,
      answer: { decision: "approve", scope: "session", option_id: "allow_always" },
      rows: () => [openRow()],
    });
    expect(outcome).toBe("answered");
    expect(posts(sent)).toEqual([{
      method: "POST",
      url: `${BASE}/sessions/session%2Fone/permission`,
      body: { tool_id: "shell-1", permission_id: "perm-1", decision: "approve", scope: "session", option_id: "allow_always" },
    }]);
  });

  it("defaults scope to once and leaves option_id out when the answer has neither", async () => {
    const { api, sent } = engine([promptArgs]);
    await respondToPermission({
      api, sessionId: "s", toolCallId: "call-1", args: promptArgs, answer: { decision: "deny" },
      rows: () => [openRow()],
    });
    expect(posts(sent)[0].body).toEqual({ tool_id: "shell-1", permission_id: "perm-1", decision: "deny", scope: "once" });
  });

  it("answers an unanswered permission prompt regardless of its row status", async () => {
    const { api, sent } = engine([promptArgs]);
    for (const status of ["complete", "running"]) {
      const outcome = await respondToPermission({
        api, sessionId: "s", toolCallId: "call-1", args: promptArgs, answer: { decision: "approve" },
        rows: () => [openRow(status)],
      });
      expect(outcome).toBe("answered");
    }
    expect(posts(sent)).toHaveLength(2);
  });

  it("returns 'stale' with no POST once the row carries its answer", async () => {
    const { api, sent } = engine([promptArgs]);
    const outcome = await respondToPermission({
      api, sessionId: "s", toolCallId: "call-1", args: promptArgs, answer: { decision: "approve" },
      rows: () => [answeredRow],
    });
    expect(outcome).toBe("stale");
    expect(sent).toEqual([]);
  });

  it("returns 'stale' with no POST when the daemon no longer lists the owner", async () => {
    const { api, sent } = engine([]);
    const outcome = await respondToPermission({
      api, sessionId: "s", toolCallId: "call-1", args: promptArgs, answer: { decision: "approve" },
      rows: () => [openRow()],
    });
    expect(outcome).toBe("stale");
    expect(posts(sent)).toEqual([]);
  });
});

describe("cancelPendingPermission", () => {
  it("sends the same request as runtime.tsx 1401-1435 and never calls onStale", async () => {
    const { api, sent } = engine([promptArgs]);
    const cancelled = await cancelPendingPermission({
      api, sessionId: "session/one", expected: [ownerOfPrompt], current: () => [ownerOfPrompt],
    });
    expect(cancelled).toBe(true);
    expect(sent).toEqual([
      { method: "GET", url: `${BASE}/sessions/session%2Fone/permissions`, body: undefined },
      { method: "POST", url: `${BASE}/sessions/session%2Fone/cancel-owned`, body: { request_id: "parent-1" } },
    ]);
  });

  it("reports an owner the daemon no longer lists by its return value, with no cancel request", async () => {
    const { api, sent } = engine([]);
    const cancelled = await cancelPendingPermission({
      api, sessionId: "s", expected: [ownerOfPrompt], current: () => [ownerOfPrompt],
    });
    expect(cancelled).toBe(false);
    expect(posts(sent)).toEqual([]);
  });
});
