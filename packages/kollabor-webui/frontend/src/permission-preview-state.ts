"use client";

import {
  isChatStreamContentPreviewMarker,
  isChatStreamPartPreviewMarker,
  isChatStreamPartsPreviewMarker,
  type ChatStreamContentPreviewMarker,
  type ChatRow,
} from "./chat-stream.ts";

export type PermissionPreviewKey = {
  sessionId: string;
  promptId: string;
  revision: string;
};

export type RecoveredPermissionPrompt = PermissionPreviewKey & {
  rowId: string;
  partIndex: number;
  data: unknown;
  /** A fetched request_permission args payload rather than a daemon prompt part. */
  partType?: "tool-call";
  /** Enclosing page revision when the recovered part came from a paged row. */
  rowRevision?: string;
  /** Outer part revision when the recovered part came from a per-part preview. */
  partRevision?: string;
  /** Exact args marker revision; distinct from the enclosing row/part revision. */
  contentRevision?: string;
  argsPreview?: ChatStreamContentPreviewMarker;
};

const visible = new Set<string>();
const recovered = new Map<string, RecoveredPermissionPrompt>();
const listeners = new Set<() => void>();
let previewStateVersion = 0;

function notify(): void {
  previewStateVersion += 1;
  for (const listener of listeners) listener();
}

/** A changing external-store snapshot so subscribers recompute from current rows. */
export function permissionPreviewStateVersion(): number {
  return previewStateVersion;
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

function hasPermissionOwner(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) &&
    "tool_id" in value && typeof value.tool_id === "string" && value.tool_id.length > 0 &&
    "permission_id" in value && typeof value.permission_id === "string" && value.permission_id.length > 0;
}

function matchesArgsPreview(
  marker: unknown,
  prompt: RecoveredPermissionPrompt,
): marker is ChatStreamContentPreviewMarker {
  return isChatStreamContentPreviewMarker(marker) && marker.field === "args" &&
    marker.session_id === prompt.sessionId && marker.row_id === prompt.rowId &&
    marker.part_index === prompt.partIndex && marker.output_revision === prompt.contentRevision;
}

/** Verify a recovered prompt against its live call or the exact page placeholder. */
export function recoveredPermissionPromptIsCurrent(
  rows: readonly ChatRow[],
  prompt: RecoveredPermissionPrompt,
): boolean {
  const row = rows.find((candidate) => candidate.id === prompt.rowId);
  const data = prompt.data;
  if (prompt.partType === "tool-call") {
    if (!row || row.kind !== "tool" || row.role !== "assistant" || !hasPermissionOwner(data)) return false;
    const expectedOuterRevision = prompt.rowRevision ?? prompt.partRevision ?? prompt.contentRevision;
    if (!expectedOuterRevision || prompt.revision !== expectedOuterRevision) return false;
    if (prompt.argsPreview && !matchesArgsPreview(prompt.argsPreview, prompt)) return false;
    if (prompt.contentRevision && !prompt.argsPreview) return false;

    if (prompt.rowRevision) {
      const rowMarker = row.chat_stream_parts_preview;
      if (!isChatStreamPartsPreviewMarker(rowMarker) || rowMarker.session_id !== prompt.sessionId ||
          rowMarker.row_id !== prompt.rowId || rowMarker.output_revision !== prompt.rowRevision ||
          prompt.partIndex >= rowMarker.part_count) return false;
      return true;
    }

    const part = row.parts[prompt.partIndex];
    if (prompt.partRevision) {
      const partMarker = part?.type === "text" ? part.chat_stream_part_preview : undefined;
      return isChatStreamPartPreviewMarker(partMarker) &&
        partMarker.session_id === prompt.sessionId && partMarker.row_id === prompt.rowId &&
        partMarker.part_index === prompt.partIndex && partMarker.output_revision === prompt.partRevision;
    }

    if (part?.type !== "tool-call" || part.tool_name !== "request_permission" ||
        part.tool_call_id !== prompt.promptId || part.result !== undefined) return false;
    // A direct live row may carry an externalized args marker. A recovered
    // complete part may instead have inline args, bound by its part revision.
    if (prompt.argsPreview) return matchesArgsPreview(part.chat_stream_args_preview, prompt);
    return prompt.contentRevision === undefined && hasPermissionOwner(part.args) &&
      JSON.stringify(part.args) === JSON.stringify(data);
  }

  const marker = row?.chat_stream_parts_preview;
  const rowRevision = prompt.rowRevision ?? (!prompt.partRevision ? prompt.revision : undefined);
  const currentOuter = rowRevision
    ? isChatStreamPartsPreviewMarker(marker) && marker.session_id === prompt.sessionId &&
      marker.row_id === prompt.rowId && marker.output_revision === rowRevision &&
      prompt.partIndex < marker.part_count
    : prompt.partRevision
      ? (() => {
          const part = row?.parts[prompt.partIndex];
          const partMarker = part?.type === "text" ? part.chat_stream_part_preview : undefined;
          return isChatStreamPartPreviewMarker(partMarker) && partMarker.session_id === prompt.sessionId &&
            partMarker.row_id === prompt.rowId && partMarker.part_index === prompt.partIndex &&
            partMarker.output_revision === prompt.partRevision;
        })()
      : false;
  return row?.kind === "prompt" && row.role === "system" && row.status === "open" && currentOuter &&
    (rowRevision ?? prompt.partRevision) === prompt.revision &&
    typeof data === "object" && data !== null && !Array.isArray(data) &&
    "permission_id" in data && data.permission_id === prompt.promptId && !("resolution" in data);
}

/** Return only recovered prompts still attached to the current open row revision. */
export function visibleRecoveredPermissionPrompts(
  rows: readonly ChatRow[],
  sessionId: string,
): RecoveredPermissionPrompt[] {
  return Array.from(recovered.values()).filter((prompt) =>
    prompt.sessionId === sessionId && visible.has(permissionPromptPreviewKey(prompt)) &&
    recoveredPermissionPromptIsCurrent(rows, prompt),
  );
}

export function subscribePermissionPromptPreview(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
