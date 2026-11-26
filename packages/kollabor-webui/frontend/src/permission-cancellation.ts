// Stop for an open permission prompt (#1451). The chat store's rows say which
// prompt is open (utils/permission-cancel.ts); the provider supplies its
// owner-scoped cancellation request through PermissionCancellationContext.

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import type { EngineApi } from "./api";
import { useChatStore, type LiveStore } from "./chat-store";
import { cancelPendingPermission } from "./permission-respond";
import { pendingPermissions, type PendingPermission } from "./utils/permission-cancel";
import {
  isPermissionPromptPreviewVisible,
  subscribePermissionPromptPreview,
} from "./permission-preview-state";

export type PermissionCancellation = {
  cancel: (expected: readonly PendingPermission[]) => Promise<void>;
  cancelling: boolean;
  error: string | null;
};

export const PermissionCancellationContext = createContext<PermissionCancellation | null>(null);

function visiblePendingPermissions(rows: readonly import("./chat-stream").ChatRow[]): PendingPermission[] {
  return pendingPermissions(rows).filter((permission) =>
    !permission.previewKey || isPermissionPromptPreviewVisible(permission.previewKey),
  );
}

/** Provides owner-scoped cancellation while the session is mounted. */
export function PermissionCancellationProvider({
  api,
  sessionId,
  store,
  children,
}: {
  api: Pick<EngineApi, "getPermissions" | "cancelOwned">;
  sessionId: string;
  store: Pick<LiveStore, "rows">;
  children: ReactNode;
}) {
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const cancel = useCallback(async (expected: readonly PendingPermission[]) => {
    if (!mounted.current) return;
    setCancelling(true);
    setError(null);
    try {
      await cancelPendingPermission({
        api,
        sessionId,
        expected,
        current: () => mounted.current ? visiblePendingPermissions(store.rows) : [],
      });
    } catch {
      if (mounted.current) setError("Could not stop the pending permission. Try again.");
    } finally {
      if (mounted.current) setCancelling(false);
    }
  }, [api, sessionId, store]);

  const control = useMemo(() => ({ cancel, cancelling, error }), [cancel, cancelling, error]);
  return createElement(PermissionCancellationContext.Provider, { value: control }, children);
}

/**
 * The Stop for the one open permission prompt, or null when no prompt or several
 * are open, or the prompt is parked (waiting on the agent's terminal).
 */
export function usePendingPermissionCancellation(): {
  cancel: () => Promise<void>;
  cancelling: boolean;
  error: string | null;
} | null {
  const control = useContext(PermissionCancellationContext);
  const { rows } = useChatStore();
  const candidates = pendingPermissions(rows);
  const expected = visiblePendingPermissions(rows);
  const previewReady = useSyncExternalStore(
    subscribePermissionPromptPreview,
    () => candidates.length === 1 && (
      !candidates[0].previewKey || isPermissionPromptPreviewVisible(candidates[0].previewKey)
    ),
    () => false,
  );
  return control && previewReady && expected.length === 1 && expected[0].parked === false
    ? { cancel: () => control.cancel(expected), cancelling: control.cancelling, error: control.error }
    : null;
}
