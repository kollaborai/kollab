import { useEffect, useState } from "react";
import { OctagonPause } from "lucide-react";
import type { EngineApi, PanelDescription } from "@/api";
import { haltedLine } from "./halted-line";

const POLL_MS = 15_000;

/** One plain line above the agent list while halted (spec "TUI and Web UI"). Rows stay as they are. */
export function HaltedLine({ api, folder, onOpen }: { api: EngineApi; folder: string; onOpen: () => void }) {
  const [panel, setPanel] = useState<PanelDescription | null>(null);
  useEffect(() => {
    if (!folder) return;
    let stop = false;
    const load = () =>
      api.haltPanel(folder).then(
        (next) => !stop && setPanel(next),
        () => !stop && setPanel(null),
      );
    void load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [api, folder]);
  const line = haltedLine(panel);
  if (!line) return null;
  return (
    <div className="text-muted-foreground flex items-start gap-2 px-2.5 py-1.5 text-xs" role="status">
      <OctagonPause className="text-destructive mt-0.5 size-3.5 shrink-0" aria-hidden />
      <p className="min-w-0 break-words">
        {line.text}.{" "}
        <button
          type="button"
          className="text-foreground underline underline-offset-2"
          onClick={() => {
            api.haltFolder = folder;
            onOpen();
          }}
        >
          {line.link}
        </button>
      </p>
    </div>
  );
}
