"use client";

import { isChatStreamPartsPreviewMarker, type ChatRow } from "./chat-stream.ts";

export type PermissionPreviewKey = {
  sessionId: string;
  promptId: string;
  revision: string;
};

export type RecoveredPermissionPrompt = PermissionPreviewKey & {
  rowId: string;
  partIndex: number;
  data: unknown;
};

const visible = new Set<string>();
const recovered = new Map<string, RecoveredPermissionPrompt>();
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

export function permissionPromptPreviewKey(key: PermissionPreviewKey): string {
  return [key.sessionId, key.promptId, key.revision].join("\u0000");
}

export function isPermissionPromptPreviewVisible(key: PermissionPreviewKey | string | undefined): boolean {
  if (!key) return false;
  return visible.has(typeof key === "string" ? key : permissionPromptPreviewKey(key));
}

export function markPermissionPromptPreviewVisible(
  key: PermissionPreviewKey,
  isVisible: boolean,
): void {
  const identity = permissionPromptPreviewKey(key);
  const changed = isVisible ? !visible.has(identity) : visible.has(identity);
  if (!changed) return;
  if (isVisible) visible.add(identity);
  else visible.delete(identity);
  notify();
}

/** Keep row-paged permission data available only while its details are open. */
export function registerRecoveredPermissionPrompt(
  prompt: RecoveredPermissionPrompt,
  isVisible: boolean,
): void {
  const identity = permissionPromptPreviewKey(prompt);
  const wasVisible = visible.has(identity);
  const prior = recovered.get(identity);
  if (isVisible) {
    visible.add(identity);
    recovered.set(identity, prompt);
  } else {
    visible.delete(identity);
    recovered.delete(identity);
  }
  if (wasVisible !== isVisible || prior !== (isVisible ? prompt : undefined)) notify();
}

/** Return only recovered prompts still attached to the current open row revision. */
export function visibleRecoveredPermissionPrompts(
  rows: readonly ChatRow[],
  sessionId: string,
): RecoveredPermissionPrompt[] {
  const rowsById = new Map(rows.map((row) => [row.id, row]));
  return Array.from(recovered.values()).filter((prompt) => {
    if (prompt.sessionId !== sessionId || !visible.has(permissionPromptPreviewKey(prompt))) {
      return false;
    }
    const row = rowsById.get(prompt.rowId);
    const marker = row?.chat_stream_parts_preview;
    const data = prompt.data;
    return row?.kind === "prompt" && row.role === "system" && row.status === "open" &&
      isChatStreamPartsPreviewMarker(marker) && marker.session_id === prompt.sessionId &&
      marker.row_id === prompt.rowId && marker.output_revision === prompt.revision &&
      prompt.partIndex < marker.part_count && typeof data === "object" && data !== null &&
      !Array.isArray(data) && "permission_id" in data && data.permission_id === prompt.promptId &&
      !("resolution" in data);
  });
}

export function subscribePermissionPromptPreview(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
