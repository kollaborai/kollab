"use client";

export type PermissionPreviewKey = {
  sessionId: string;
  promptId: string;
  revision: string;
};

const visible = new Set<string>();
const listeners = new Set<() => void>();

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
  for (const listener of listeners) listener();
}

export function subscribePermissionPromptPreview(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
