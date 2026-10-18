import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { errorText, type EngineApi, type HiddenAgent } from "@/api";
import { dropLegacyHiddenAgents } from "@/components/shell/hidden-agents";

/**
 * The sidebar rows hidden for every client of this engine (engine:
 * hidden_agents.py). The list rides GET /sessions, so each client's session
 * refresh is also its sync. A hide or show applies from the engine's answer at
 * once, and a refresh that began before it (`stamp`) cannot bring the old list
 * back.
 */
export function useHiddenAgents(api: EngineApi) {
  const [hidden, setHidden] = useState<HiddenAgent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const edits = useRef(0);
  useEffect(dropLegacyHiddenAgents, []);

  const stamp = useCallback(() => edits.current, []);
  const sync = useCallback((from: number, list: HiddenAgent[] | undefined) => {
    if (list && from === edits.current) setHidden(list);
  }, []);

  const change = useCallback(async (request: () => Promise<{ hidden: HiddenAgent[] }>) => {
    edits.current += 1;
    setError(null);
    try {
      setHidden((await request()).hidden);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      edits.current += 1;
    }
  }, []);

  return useMemo(() => ({
    hidden,
    /** Why the last hide or show failed. */
    error,
    stamp,
    sync,
    hide: (entry: HiddenAgent) => change(() => api.hideAgent(entry)),
    show: (key: string) => change(() => api.showAgent(key)),
    showAll: () => change(() => api.showAllAgents()),
  }), [api, change, error, hidden, stamp, sync]);
}

export type HiddenAgents = ReturnType<typeof useHiddenAgents>;
