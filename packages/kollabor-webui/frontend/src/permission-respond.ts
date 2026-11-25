import type { EngineApi } from "./api.ts";
import type { ChatPart, ChatRow } from "./chat-stream.ts";
import type { PendingPermission } from "./utils/permission-cancel.ts";
import { cancelPendingPermission as cancelIfStillPending, permissionOwner } from "./utils/permission-cancel.ts";

/** The status an open permission prompt row carries. Any other status is no longer answerable. */
const OPEN_PROMPT_STATUS = "requires-action";

export type PermissionAnswer = { decision: "approve" | "deny"; scope?: string; option_id?: string };

type ToolCallPart = Extract<ChatPart, { type: "tool-call" }>;
type PermissionResponseOwner = PendingPermission & { permissionId: string };

/** Answers in flight, keyed by session and permission: one POST per permission at a time. */
const responsesInFlight = new Set<string>();

// Lifted from runtime.tsx 125-151.
const permissionResponseOwner = (payload: unknown): PermissionResponseOwner | null => {
  const owner = permissionOwner(payload);
  if (!owner || !payload || typeof payload !== "object") return null;
  const value = payload as { request_id?: unknown; permission_id?: unknown; details?: unknown };
  const details = value.details && typeof value.details === "object"
    ? value.details as { permission_id?: unknown }
    : undefined;
  const permissionId = typeof value.permission_id === "string" && value.permission_id
    ? value.permission_id
    : typeof details?.permission_id === "string" && details.permission_id
      ? details.permission_id
      : "";
  if (!permissionId) return null;
  const requestId = value.request_id;
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

/** The unanswered request_permission call with this tool call ID, if the row has one. */
function openPrompt(row: ChatRow, toolCallId: string): ToolCallPart | undefined {
  return row.parts.find((part): part is ToolCallPart => part.type === "tool-call" &&
    part.tool_call_id === toolCallId && part.tool_name === "request_permission" && part.result === undefined);
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
      const part = openPrompt(row, toolCallId);
      if (part) return { row, part };
    }
    return undefined;
  };
  const initial = findPrompt();
  if (!initial || initial.row.status !== OPEN_PROMPT_STATUS) return "stale";
  const expected = permissionResponseOwner(initial.part.args);
  const displayed = permissionResponseOwner(args);
  if (!expected || !displayed || !samePermissionResponseOwner(expected, [displayed])) return "stale";
  const key = `${sessionId}:${expected.permissionId}`;
  if (responsesInFlight.has(key)) return "stale";
  const isCurrent = () => {
    const current = findPrompt();
    if (!current || current.row.id !== initial.row.id || current.row.status !== OPEN_PROMPT_STATUS) return false;
    const owner = permissionResponseOwner(current.part.args);
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
 * Lifted from the daemon read and cancel request in runtime.tsx cancelPermission
 * (1401-1435). Without its onStale and importExternalState calls: a stale owner
 * resolves false, and the caller decides what to show.
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
      const owner = permissionOwner(prompt);
      return owner ? [owner] : [];
    }),
    cancel: (requestId) => api.cancelOwned(sessionId, requestId),
  });
}
