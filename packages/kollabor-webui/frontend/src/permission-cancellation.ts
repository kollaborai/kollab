// Stop for an open permission prompt (#1451). The chat store's rows say which
// prompt is open (utils/permission-cancel.ts); App provides the request that
// cancels it, through PermissionCancellationContext.

import { createContext, useContext } from "react";
import { useChatStore } from "./chat-store";
import { pendingPermissions, type PendingPermission } from "./utils/permission-cancel";

export type PermissionCancellation = {
  cancel: (expected: readonly PendingPermission[]) => Promise<void>;
  cancelling: boolean;
};

export const PermissionCancellationContext = createContext<PermissionCancellation | null>(null);

/**
 * The Stop for the one open permission prompt, or null when no prompt or several
 * are open, or the prompt is parked (waiting on the agent's terminal).
 */
export function usePendingPermissionCancellation(): { cancel: () => Promise<void>; cancelling: boolean } | null {
  const control = useContext(PermissionCancellationContext);
  const { rows } = useChatStore();
  const expected = pendingPermissions(rows);
  return control && expected.length === 1 && expected[0].parked === false
    ? { cancel: () => control.cancel(expected), cancelling: control.cancelling }
    : null;
}
