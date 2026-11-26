import type { EngineApi } from "./api.ts";
import type { ChatPart, ChatRow } from "./chat-stream.ts";
import type { PendingPermission } from "./utils/permission-cancel.ts";
import { cancelPendingPermission as cancelIfStillPending, permissionOwner } from "./utils/permission-cancel.ts";

export type PermissionAnswer = { decision: "approve" | "deny"; scope?: string; option_id?: string };

type PermissionResponseOwner = PendingPermission & { permissionId: string };

/** Answers in flight, keyed by session and permission: one POST per permission at a time. */
const responsesInFlight = new Set<string>();

// Lifted from runtime.tsx 125-151.
const permissionResponseOwner = (payload: unknown): PermissionResponseOwner | null => {
  const owner = permissionOwner(payload);
  if (!owner || !payload || typeof payload !== "object") return null;
  const value = payload as { type?: unknown; request_id?: unknown; permission_id?: unknown; details?: unknown };
  const details = value.details && typeof value.details === "object"
    ? value.details as { permission_id?: unknown; request_id?: unknown }
    : undefined;
  const permissionId = typeof value.permission_id === "string" && value.permission_id
    ? value.permission_id
    : typeof details?.permission_id === "string" && details.permission_id
      ? details.permission_id
      : "";
  if (!permissionId) return null;
  // The GET endpoint returns the normalized permission event: request_id may
  // live in its details because the daemon's original event carries it there.
  // Row prompt data is already that raw details object, and tool-call args use
  // the top-level owner id, so don't infer parentage from arbitrary nested args.
  const requestId = value.request_id ?? (value.type === "permission_request" ? details?.request_id : undefined);
  return {
    ...owner,
    ...(typeof requestId === "string" && requestId ? { requestId } : {}),
    permissionId,
  };
};

const samePermissionResponseOwner = (
  expected: PermissionResponseOwner,
  candidates: readonly PermissionResponseOwner[],
) => candidates.some((candidate) => candidate.toolId === expected.toolId &&
  candidate.requestId === expected.requestId && candidate.source === expected.source &&
  candidate.permissionId === expected.permissionId);

/** The open permission call or daemon prompt part with this identifier, if present. */
function openPermissionPart(row: ChatRow, toolCallId: string): { payload: unknown } | undefined {
  const toolCall = row.parts.find((part) => part.type === "tool-call" &&
    part.tool_call_id === toolCallId && part.tool_name === "request_permission" && part.result === undefined);
  if (toolCall?.type === "tool-call") return { payload: toolCall.args };
  const prompt = row.parts.find((part) => part.type === "prompt" && part.prompt_id === toolCallId);
  if (prompt?.type !== "prompt" || row.kind !== "prompt" || row.role !== "system" || row.status !== "open") return undefined;
  if (!prompt.data || typeof prompt.data !== "object" || Array.isArray(prompt.data) || "resolution" in prompt.data) return undefined;
  const data = prompt.data as { permission_id?: unknown };
  if (data.permission_id !== toolCallId) return undefined;
  return { payload: prompt.data };
}

/**
 * Sends one answer to a permission prompt. Lifted from runtime.tsx
 * respondToPermission (1437-1492), reading rows instead of messages. The prompt
 * is read again after each await: a prompt answered or replaced meanwhile, or
 * one the daemon no longer lists, is "stale" and sends nothing.
 */
export async function respondToPermission({
  api,
  sessionId,
  toolCallId,
  args,
  answer,
  rows,
}: {
  api: Pick<EngineApi, "getPermissions" | "request">;
  sessionId: string;
  toolCallId: string;
  args: unknown;
  answer: PermissionAnswer;
  rows: () => readonly ChatRow[];
}): Promise<"answered" | "stale"> {
  const findPrompt = () => {
    for (const row of rows()) {
      const part = openPermissionPart(row, toolCallId);
      if (part) return { row, part };
    }
    return undefined;
  };
  const initial = findPrompt();
  if (!initial) return "stale";
  const expected = permissionResponseOwner(initial.part.payload);
  const displayed = permissionResponseOwner(args);
  if (!expected || !displayed || !samePermissionResponseOwner(expected, [displayed])) return "stale";
  const key = `${sessionId}:${expected.permissionId}`;
  if (responsesInFlight.has(key)) return "stale";
  const isCurrent = () => {
    const current = findPrompt();
    if (!current || current.row.id !== initial.row.id) return false;
    const owner = permissionResponseOwner(current.part.payload);
    return !!owner && samePermissionResponseOwner(expected, [owner]);
  };
  if (!isCurrent()) return "stale";

  responsesInFlight.add(key);
  try {
    const permissions = await api.getPermissions(sessionId);
    const owners = permissions.pending_prompts.flatMap((prompt) => {
      const owner = permissionResponseOwner(prompt);
      return owner ? [owner] : [];
    });
    if (!isCurrent() || !samePermissionResponseOwner(expected, owners)) return "stale";
    await api.request(`/sessions/${encodeURIComponent(sessionId)}/permission`, {
      method: "POST",
      body: JSON.stringify({
        tool_id: expected.toolId,
        permission_id: expected.permissionId,
        decision: answer.decision,
        scope: answer.scope ?? "once",
        ...(answer.option_id ? { option_id: answer.option_id } : {}),
      }),
    });
    return "answered";
  } finally {
    responsesInFlight.delete(key);
  }
}

/**
 * Cancels the one pending permission the page shows, on its parent request ID.
 * Lifted from the daemon read and cancel request the retired engine runtime sent.
 * A stale owner resolves false, and the caller decides what to show.
 */
export function cancelPendingPermission({
  api,
  sessionId,
  expected,
  current,
}: {
  api: Pick<EngineApi, "getPermissions" | "cancelOwned">;
  sessionId: string;
  expected: readonly PendingPermission[];
  current: () => readonly PendingPermission[];
}): Promise<boolean> {
  return cancelIfStillPending({
    expected,
    current,
    readPending: async () => (await api.getPermissions(sessionId)).pending_prompts.flatMap((prompt) => {
      const owner = permissionResponseOwner(prompt);
      return owner ? [{
        toolId: owner.toolId,
        requestId: owner.requestId,
        source: owner.source,
        parked: owner.parked,
      }] : [];
    }),
    cancel: (requestId) => api.cancelOwned(sessionId, requestId),
  });
}
