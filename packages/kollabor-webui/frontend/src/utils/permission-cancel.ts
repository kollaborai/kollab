import type { ChatRow } from "../chat-stream.ts";
import {
  isChatStreamPartPreviewMarker,
  isChatStreamToolOutputPreviewMarker,
} from "../chat-stream.ts";
import {
  permissionPromptPreviewKey,
  recoveredPermissionPromptIsCurrent,
  type RecoveredPermissionPrompt,
} from "../permission-preview-state.ts";

export type PendingPermission = {
  toolId: string;
  requestId?: string;
  source?: string;
  parked?: boolean;
  previewKey?: string;
};

export function permissionOwner(payload: unknown): PendingPermission | null {
  if (!payload || typeof payload !== "object") return null;
  const args = payload as {
    tool_id?: unknown;
    request_id?: unknown;
    source?: unknown;
    parked?: unknown;
    details?: { source?: unknown; parked?: unknown };
  };
  if (typeof args.tool_id !== "string" || !args.tool_id) return null;
  const details = args.details;
  return {
    toolId: args.tool_id,
    requestId: typeof args.request_id === "string" && args.request_id ? args.request_id : undefined,
    source: typeof args.source === "string" ? args.source : typeof details?.source === "string" ? details.source : undefined,
    ...(typeof args.parked === "boolean"
      ? { parked: args.parked }
      : typeof details?.parked === "boolean" ? { parked: details.parked } : {}),
  };
}

/**
 * A permission part is pending while its request_permission tool call has no
 * result. Only a pending part with a parent owner exposes Stop.
 */
export function pendingPermissions(
  rows: readonly ChatRow[],
  recoveredPrompts: readonly RecoveredPermissionPrompt[] = [],
): PendingPermission[] {
  const fromRows = rows.flatMap((row) => row.parts.flatMap((part) => {
    let payload: unknown;
    if (part.type === "tool-call") {
      if (part.tool_name !== "request_permission" || part.result !== undefined ||
          (isChatStreamToolOutputPreviewMarker(part.chat_stream_result_preview) &&
            part.chat_stream_result_preview.tool_call_id === part.tool_call_id)) return [];
      // A compact owner summary is only an association hint. The complete args
      // must be fetched and the matching preview opened before Stop is enabled.
      if (part.chat_stream_args_preview !== undefined) return [];
      payload = part.args;
    } else if (part.type === "prompt") {
      if (row.kind !== "prompt" || row.role !== "system" || row.status !== "open" ||
          (typeof part.data === "object" && part.data !== null && "resolution" in part.data)) return [];
      payload = part.data;
    } else {
      return [];
    }
    const owner = permissionOwner(payload);
    if (!owner?.requestId) return [];
    if (part.type === "prompt" && isChatStreamPartPreviewMarker(part.chat_stream_part_preview)) {
      return [{
        ...owner,
        previewKey: permissionPromptPreviewKey({
          sessionId: part.chat_stream_part_preview.session_id,
          promptId: part.prompt_id,
          revision: part.chat_stream_part_preview.output_revision,
        }),
      }];
    }
    return [owner];
  }));
  const fromRecovered = recoveredPrompts.flatMap((prompt) => {
    const data = prompt.data;
    if (prompt.partType === "tool-call") {
      if (!recoveredPermissionPromptIsCurrent(rows, prompt) || typeof data !== "object" || data === null || Array.isArray(data)) return [];
      const owner = permissionOwner(data);
      if (!owner?.requestId || !("permission_id" in data) || typeof data.permission_id !== "string" || !data.permission_id) return [];
      return [{ ...owner, previewKey: permissionPromptPreviewKey(prompt) }];
    }
    if (
      !recoveredPermissionPromptIsCurrent(rows, prompt) || typeof data !== "object" || data === null ||
      Array.isArray(data) || !("permission_id" in data) || data.permission_id !== prompt.promptId ||
      "resolution" in data
    ) return [];
    const owner = permissionOwner(data);
    if (!owner?.requestId) return [];
    return [{
      ...owner,
      previewKey: permissionPromptPreviewKey(prompt),
    }];
  });
  return [...fromRows, ...fromRecovered];
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
  if (expected.length !== 1 || expected[0].parked !== false) return false;
  const selected = expected[0];
  const beforeRead = current();
  if (!samePermissionOwner(selected, beforeRead)) return false;
  const daemon = await readPending();
  const afterRead = current();
  const daemonOwner = daemon.find((candidate) => sameDaemonPermissionOwner(selected, candidate));
  if (!selected.requestId || !samePermissionOwner(selected, afterRead) || daemonOwner?.parked !== false) return false;
  return (await cancel(selected.requestId)).cancelled === true;
}

function sameDaemonPermissionOwner(expected: PendingPermission, candidate: PendingPermission): boolean {
  return candidate.toolId === expected.toolId && candidate.requestId === expected.requestId &&
    candidate.source === expected.source && candidate.parked === expected.parked;
}

function samePermissionOwner(expected: PendingPermission, candidates: readonly PendingPermission[]): boolean {
  return candidates.some((candidate) => candidate.toolId === expected.toolId &&
    candidate.requestId === expected.requestId && candidate.source === expected.source &&
    candidate.parked === expected.parked && candidate.previewKey === expected.previewKey);
}

export function samePendingPermission(a: readonly PendingPermission[], b: readonly PendingPermission[]): boolean {
  return a.some((left) => b.some((right) => left.toolId === right.toolId &&
    left.requestId === right.requestId && left.source === right.source));
}
