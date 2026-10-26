import { useCallback, useMemo, useRef, useState } from "react";
import { ApiError, errorText, type EngineApi } from "@/api";
import {
  createSidebarOrderController,
  initialSidebarOrder,
  type SidebarOrderClient,
  type SidebarOrderSnapshot,
} from "@/components/shell/sidebar-order-controller";

/** The engine-owned order is restored from GET /sessions for every client. */
export function useSidebarOrder(api: EngineApi) {
  const [state, setState] = useState(initialSidebarOrder);
  const controllerRef = useRef<ReturnType<typeof createSidebarOrderController> | null>(null);
  if (controllerRef.current === null) {
    const client: SidebarOrderClient = {
      saveSidebarOrder: (order, revision) => api.saveSidebarOrder(order, revision),
      getSidebarOrder: () => api.getSidebarOrder(),
      isConflict: (reason) => reason instanceof ApiError && reason.status === 409,
      isRecoveryRequired: (reason) => reason instanceof ApiError && reason.status === 422,
      errorText,
    };
    controllerRef.current = createSidebarOrderController(client, setState);
  }
  const controller = controllerRef.current;
  const stamp = useCallback(() => controller.stamp(), [controller]);
  const sync = useCallback(
    (from: number, snapshot: SidebarOrderSnapshot | undefined) => controller.sync(from, snapshot),
    [controller],
  );
  const save = useCallback((next: string[]) => controller.save(next), [controller]);
  const beginDrag = useCallback(() => controller.beginDrag(), [controller]);
  return useMemo(() => ({ ...state, stamp, sync, save, beginDrag }), [state, stamp, sync, save, beginDrag]);
}

export type SidebarOrder = ReturnType<typeof useSidebarOrder>;
