import { useCallback, useMemo, useRef, useState } from "react";
import { errorText, type EngineApi } from "@/api";

/** The engine-owned order is sent with GET /sessions and shared by its clients. */
export function useSidebarOrder(api: EngineApi) {
  const [order, setOrder] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const edits = useRef(0);
  const pendingWrites = useRef(0);
  const latestSave = useRef(0);
  const saveQueue = useRef(Promise.resolve());
  const stamp = useCallback(() => edits.current, []);
  const sync = useCallback((from: number, list: string[] | undefined) => {
    // Do not let a poll started during an in-flight write overwrite the user's
    // optimistic order with an older engine snapshot.
    if (list && pendingWrites.current === 0 && from === edits.current) {
      setOrder((cur) => (JSON.stringify(cur) === JSON.stringify(list) ? cur : list));
    }
  }, []);
  const save = useCallback(async (next: string[]) => {
    const saveId = ++latestSave.current;
    edits.current += 1;
    pendingWrites.current += 1;
    setOrder(next);
    setError(null);

    // Serialize writes so a slow response cannot persist an earlier drag/menu
    // action after a newer one.
    const request = saveQueue.current.then(() => api.saveSidebarOrder(next));
    saveQueue.current = request.then(() => undefined, () => undefined);
    try {
      const result = await request;
      if (saveId === latestSave.current) setOrder(result.order);
    } catch (reason) {
      if (saveId === latestSave.current) setError(errorText(reason));
    } finally {
      pendingWrites.current -= 1;
    }
  }, [api]);
  return useMemo(() => ({ order, error, stamp, sync, save }), [order, error, stamp, sync, save]);
}

export type SidebarOrder = ReturnType<typeof useSidebarOrder>;
