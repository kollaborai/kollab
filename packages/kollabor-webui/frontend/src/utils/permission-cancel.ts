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

export function samePendingPermission(a: readonly PendingPermission[], b: readonly PendingPermission[]): boolean {
  return a.some((left) => b.some((right) => left.toolId === right.toolId &&
    left.requestId === right.requestId && left.source === right.source));
}
