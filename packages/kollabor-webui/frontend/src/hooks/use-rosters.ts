import { useCallback, useEffect, useState } from "react";
import type { EngineApi, RostersResponse } from "@/api";

/**
 * The rosters one folder can draw agents from (GET /rosters: the bundled and user
 * libraries, plus the folder's own project files). It loads again when the folder
 * changes and when this tab is shown again, since the files are edited outside the
 * page; an engine without the route leaves it null, and New Session lists the
 * folder's own agents alone.
 */
export function useRosters(api: EngineApi, workspace: string) {
  const [library, setLibrary] = useState<RostersResponse | null>(null);
  const [visit, setVisit] = useState(0);
  const reload = useCallback(() => setVisit((count) => count + 1), []);

  useEffect(() => {
    let stale = false;
    api
      .listRosters(workspace)
      .then((result) => {
        if (stale) return;
        // The poll that follows a tab switch usually finds the same files.
        setLibrary((current) => (JSON.stringify(current) === JSON.stringify(result) ? current : result));
      })
      .catch(() => {});
    return () => {
      stale = true;
    };
  }, [api, workspace, visit]);

  useEffect(() => {
    const shown = () => {
      if (document.visibilityState === "visible") reload();
    };
    document.addEventListener("visibilitychange", shown);
    return () => document.removeEventListener("visibilitychange", shown);
  }, [reload]);

  return { library, reload };
}
