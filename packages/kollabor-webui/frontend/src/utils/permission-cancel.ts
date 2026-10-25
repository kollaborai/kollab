export type PendingPermission = { toolId: string; requestId?: string; source?: string };

type PermissionMessage = {
  status?: { type: string };
  content: readonly {
    type: string;
    toolName?: string;
    args?: unknown;
    result?: unknown;
  }[];
};

export function permissionOwner(payload: unknown): PendingPermission | null {
  if (!payload || typeof payload !== "object") return null;
  const args = payload as { tool_id?: unknown; details?: { request_id?: unknown; source?: unknown } };
  if (typeof args.tool_id !== "string" || !args.tool_id) return null;
  return {
    toolId: args.tool_id,
    requestId: typeof args.details?.request_id === "string" && args.details.request_id ? args.details.request_id : undefined,
    source: typeof args.details?.source === "string" ? args.details.source : undefined,
  };
}

/** Only unresolved permission parts with a parent owner expose Stop. */
export function pendingPermissions(messages: readonly PermissionMessage[]): PendingPermission[] {
  return messages.flatMap((message) => message.status?.type === "requires-action"
    ? message.content.flatMap((part) => {
      if (part.type !== "tool-call" || part.toolName !== "request_permission" || part.result !== undefined) return [];
      const owner = permissionOwner(part.args);
      return owner?.requestId ? [owner] : [];
    })
    : []);
}

/**
 * Recheck the rendered prompt against both the daemon and current thread.
 * ACP supplies a parent request ID for the daemon's owner-scoped cancellation.
 * A permission without a parent owner cannot use this control.
 */
export async function cancelPendingPermission({
  expected,
  current,
  readPending,
  cancel,
}: {
  expected: readonly PendingPermission[];
  current: () => readonly PendingPermission[];
  readPending: () => Promise<readonly PendingPermission[]>;
  cancel: (requestId: string) => Promise<{ cancelled?: boolean }>;
}): Promise<boolean> {
  if (!expected.length || !samePendingPermission(expected, current())) return false;
  const daemon = await readPending();
  const owner = expected.find((candidate) =>
    samePendingPermission([candidate], daemon) && samePendingPermission([candidate], current()));
  if (!owner?.requestId) return false;
  return (await cancel(owner.requestId)).cancelled !== false;
}

type ShownMessage = { id?: unknown; status?: { type?: string }; content: unknown };
type PromptPart = { type?: string; toolName?: string; result?: unknown; args?: { details?: { parked?: unknown } } };

/**
 * The thread shows a prompt its own run exposed, and the reloaded history has
 * one its turn left open (a Stop, or 5 min unanswered). That run ended at the
 * prompt with no sync point, so only a whole reload shows what the turn did
 * after it: the call's "Not run yet" and the agent's ask for the OK.
 */
export function promptParkedSinceShown(shown: readonly ShownMessage[], reloaded: readonly ShownMessage[]): boolean {
  const last = shown.at(-1);
  // A loaded prompt is its own message (`permission-<tool id>-<n>`) and came
  // with the history: nothing is missing above it.
  if (!last || last.status?.type !== "requires-action" || String(last.id ?? "").startsWith("permission-")) return false;
  return reloaded.some((message) => message.status?.type === "requires-action" && Array.isArray(message.content) &&
    (message.content as PromptPart[]).some((part) => part?.type === "tool-call" &&
      part.toolName === "request_permission" && part.result === undefined && part.args?.details?.parked === true));
}

export function samePendingPermission(a: readonly PendingPermission[], b: readonly PendingPermission[]): boolean {
  return a.some((left) => b.some((right) => left.toolId === right.toolId &&
    left.requestId === right.requestId && left.source === right.source));
}
