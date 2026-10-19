import { useEffect, useState } from "react";
import type { EngineApi, EngineConfig } from "@/api";
import { versionRows } from "@/utils/versions";

/** Versions of the software behind this page: kollab, engine, web UI server and bundle. */
export function VersionsSection({ api }: { api: EngineApi }) {
  const [config, setConfig] = useState<EngineConfig | null>(null);

  useEffect(() => {
    let active = true;
    void api.loadConfig().then((next) => {
      if (active) setConfig(next);
    });
    return () => {
      active = false;
    };
  }, [api]);

  return (
    <section aria-label="Versions" className="grid gap-2 border-t pt-3">
      <h4 className="text-muted-foreground text-xs font-medium">Versions</h4>
      <div className="grid grid-cols-[7rem_minmax(0,1fr)] items-center gap-2 sm:grid-cols-[8rem_minmax(0,1fr)]">
        {versionRows(config, __APP_VERSION__).map((row) => (
          <span key={row.label} className="contents">
            <span className="text-muted-foreground">{row.label}</span>
            <span className="font-mono break-all">{row.value}</span>
          </span>
        ))}
      </div>
    </section>
  );
}
