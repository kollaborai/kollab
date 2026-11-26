import { afterEach, describe, expect, it, vi } from "vitest";
import { EngineApi } from "../src/api.ts";
import type { ChatRow } from "../src/chat-stream.ts";
import { cancelPendingPermission, respondToPermission } from "../src/permission-respond.ts";
import { pendingPermissions } from "../src/utils/permission-cancel.ts";

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
// `ChatStreamService._on_permission_prompt` stores these raw details directly
// in its system/prompt row. The permissions endpoint returns the normalized
// event shape, with those same details nested under `details`.
const daemonPromptData = {
  tool_id: "daemon-shell-1",
  permission_id: "daemon-perm-1",
  request_id: "daemon-parent-1",
  source: "acp",
  parked: false,
  tool_name: "terminal",
  tool_type: "shell",
  risk_level: "medium",
  command: "pytest -q",
  acp_options: [{ optionId: "allow-once", name: "Allow once", kind: "allow_once" }],
};
const daemonPromptRow: ChatRow = {
  id: "prompt-daemon-perm-1", seq: 4, kind: "prompt", role: "system", turn_id: "turn-1", status: "open",
  parts: [{ type: "prompt", prompt_id: "daemon-perm-1", data: daemonPromptData }],
  created_at: "2026-11-25T00:00:00Z",
};
const pagedPromptMarker = {
  kind: "chat-stream-parts-preview" as const,
  session_id: "session/one",
  row_id: "paged-prompt-row",
  part_count: 1,
  output_bytes: 300_000,
  output_chars: 300_000,
  output_type: "json" as const,
  output_revision: "a".repeat(64),
};
const pagedPromptRow: ChatRow = {
  id: pagedPromptMarker.row_id,
  seq: 5,
  kind: "prompt",
  role: "system",
  turn_id: null,
  status: "open",
  parts: [{ type: "text", text: "[Large chat row]" }],
  chat_stream_parts_preview: pagedPromptMarker,
  created_at: "2026-11-25T00:00:00Z",
};
const recoveredPagedPrompt = {
  sessionId: pagedPromptMarker.session_id,
  rowId: pagedPromptMarker.row_id,
  revision: pagedPromptMarker.output_revision,
  promptId: daemonPromptData.permission_id,
  partIndex: 0,
  data: daemonPromptData,
};

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
  it("answers the daemon-produced system prompt row using its prompt ID and raw details", async () => {
    const daemonEvent = {
      type: "permission_request",
      tool_id: daemonPromptData.tool_id,
      permission_id: daemonPromptData.permission_id,
      tool_name: daemonPromptData.tool_name,
      details: daemonPromptData,
    };
    const { api, sent } = engine([daemonEvent]);
    const outcome = await respondToPermission({
      api,
      sessionId: "session/one",
      toolCallId: daemonPromptData.permission_id,
      args: daemonPromptData,
      answer: { decision: "approve", scope: "once", option_id: "allow-once" },
      rows: () => [daemonPromptRow],
    });
    expect(outcome).toBe("answered");
    expect(posts(sent)).toEqual([{
      method: "POST",
      url: `${BASE}/sessions/session%2Fone/permission`,
      body: {
        tool_id: "daemon-shell-1",
        permission_id: "daemon-perm-1",
        decision: "approve",
        scope: "once",
        option_id: "allow-once",
      },
    }]);
  });

  it("does not answer a daemon prompt after the same row receives a resolution", async () => {
    const { api, sent } = engine([{
      type: "permission_request",
      tool_id: daemonPromptData.tool_id,
      permission_id: daemonPromptData.permission_id,
      details: daemonPromptData,
    }]);
    const resolvedRow: ChatRow = {
      ...daemonPromptRow,
      status: "denied",
      parts: [{
        type: "prompt",
        prompt_id: daemonPromptData.permission_id,
        data: { ...daemonPromptData, resolution: { type: "permission_denied", tool_id: daemonPromptData.tool_id } },
      }],
    };
    const outcome = await respondToPermission({
      api,
      sessionId: "s",
      toolCallId: daemonPromptData.permission_id,
      args: daemonPromptData,
      answer: { decision: "approve" },
      rows: () => [resolvedRow],
    });
    expect(outcome).toBe("stale");
    expect(sent).toEqual([]);
  });

  it("answers a verified permission prompt recovered from a paged row", async () => {
    const daemonEvent = {
      type: "permission_request",
      tool_id: daemonPromptData.tool_id,
      permission_id: daemonPromptData.permission_id,
      details: daemonPromptData,
    };
    const { api, sent } = engine([daemonEvent]);
    const outcome = await respondToPermission({
      api,
      sessionId: "session/one",
      toolCallId: daemonPromptData.permission_id,
      args: daemonPromptData,
      answer: { decision: "approve" },
      rows: () => [pagedPromptRow],
      recoveredPrompts: () => [recoveredPagedPrompt],
    } as Parameters<typeof respondToPermission>[0]);

    expect(outcome).toBe("answered");
    expect(posts(sent)).toEqual([{
      method: "POST",
      url: `${BASE}/sessions/session%2Fone/permission`,
      body: {
        tool_id: daemonPromptData.tool_id,
        permission_id: daemonPromptData.permission_id,
        decision: "approve",
        scope: "once",
      },
    }]);
  });

  it("does not answer after the recovered row revision changes during lookup", async () => {
    const daemonEvent = {
      type: "permission_request",
      tool_id: daemonPromptData.tool_id,
      permission_id: daemonPromptData.permission_id,
      details: daemonPromptData,
    };
    const { api, sent } = engine([daemonEvent]);
    let rows: ChatRow[] = [pagedPromptRow];
    const getPermissions = api.getPermissions.bind(api);
    vi.spyOn(api, "getPermissions").mockImplementation(async (sessionId) => {
      const result = await getPermissions(sessionId);
      rows = [{
        ...pagedPromptRow,
        chat_stream_parts_preview: {
          ...pagedPromptMarker,
          output_revision: "b".repeat(64),
        },
      }];
      return result;
    });
    const outcome = await respondToPermission({
      api,
      sessionId: "session/one",
      toolCallId: daemonPromptData.permission_id,
      args: daemonPromptData,
      answer: { decision: "approve" },
      rows: () => rows,
      recoveredPrompts: () => [recoveredPagedPrompt],
    } as Parameters<typeof respondToPermission>[0]);

    expect(outcome).toBe("stale");
    expect(api.getPermissions).toHaveBeenCalledOnce();
    expect(posts(sent)).toEqual([]);
  });

  it("does not answer a recovered prompt after its details are closed", async () => {
    const daemonEvent = {
      type: "permission_request",
      tool_id: daemonPromptData.tool_id,
      permission_id: daemonPromptData.permission_id,
      details: daemonPromptData,
    };
    const { api, sent } = engine([daemonEvent]);
    let recovered = [recoveredPagedPrompt];
    const getPermissions = api.getPermissions.bind(api);
    vi.spyOn(api, "getPermissions").mockImplementation(async (sessionId) => {
      const result = await getPermissions(sessionId);
      recovered = [];
      return result;
    });
    const outcome = await respondToPermission({
      api,
      sessionId: "session/one",
      toolCallId: daemonPromptData.permission_id,
      args: daemonPromptData,
      answer: { decision: "approve" },
      rows: () => [pagedPromptRow],
      recoveredPrompts: () => recovered,
    } as Parameters<typeof respondToPermission>[0]);

    expect(outcome).toBe("stale");
    expect(api.getPermissions).toHaveBeenCalledOnce();
    expect(posts(sent)).toEqual([]);
  });

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
  it("exposes only open daemon prompt rows with a live parent owner to Stop", () => {
    const resolved: ChatRow = {
      ...daemonPromptRow,
      status: "denied",
      parts: [{
        type: "prompt",
        prompt_id: daemonPromptData.permission_id,
        data: { ...daemonPromptData, resolution: { type: "permission_denied" } },
      }],
    };
    const missingOwner: ChatRow = {
      ...daemonPromptRow,
      id: "prompt-no-parent",
      parts: [{ type: "prompt", prompt_id: "no-parent", data: { tool_id: "tool-without-parent" } }],
    };
    expect(pendingPermissions([daemonPromptRow, resolved, missingOwner])).toEqual([
      { toolId: "daemon-shell-1", requestId: "daemon-parent-1", source: "acp", parked: false },
    ]);
  });

  it("cancels an open daemon prompt by the parent request ID from the prompt details", async () => {
    const daemonEvent = {
      type: "permission_request",
      tool_id: daemonPromptData.tool_id,
      permission_id: daemonPromptData.permission_id,
      details: daemonPromptData,
    };
    const { api, sent } = engine([daemonEvent]);
    const daemonOwner = { toolId: "daemon-shell-1", requestId: "daemon-parent-1", source: "acp", parked: false };
    const cancelled = await cancelPendingPermission({
      api,
      sessionId: "session/one",
      expected: [daemonOwner],
      current: () => [daemonOwner],
    });
    expect(cancelled).toBe(true);
    expect(sent).toEqual([
      { method: "GET", url: `${BASE}/sessions/session%2Fone/permissions`, body: undefined },
      { method: "POST", url: `${BASE}/sessions/session%2Fone/cancel-owned`, body: { request_id: "daemon-parent-1" } },
    ]);
  });

  it("compares a recovered prompt's preview key only against the current row, not daemon owner", async () => {
    const daemonEvent = {
      type: "permission_request",
      tool_id: daemonPromptData.tool_id,
      permission_id: daemonPromptData.permission_id,
      details: daemonPromptData,
    };
    const { api, sent } = engine([daemonEvent]);
    const recoveredOwner = {
      toolId: daemonPromptData.tool_id,
      requestId: daemonPromptData.request_id,
      source: "acp",
      parked: false,
      previewKey: "session/one\u0000daemon-perm-1\u0000" + "a".repeat(64),
    };
    const cancelled = await cancelPendingPermission({
      api,
      sessionId: "session/one",
      expected: [recoveredOwner],
      current: () => [recoveredOwner],
    });

    expect(cancelled).toBe(true);
    expect(posts(sent)).toEqual([{
      method: "POST",
      url: `${BASE}/sessions/session%2Fone/cancel-owned`,
      body: { request_id: daemonPromptData.request_id },
    }]);
  });

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
