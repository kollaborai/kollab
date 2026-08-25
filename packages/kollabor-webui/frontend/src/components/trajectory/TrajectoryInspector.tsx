import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { TrajectoryRecord } from "./trajectory-records";
import {
  formatDuration,
  formatKind,
  formatTimestamp,
} from "./trajectory-format";
import { cn } from "@/lib/utils";

type DetailTab = "input" | "output" | "thinking";

const DETAIL_TABS: Array<{ id: DetailTab; label: string }> = [
  { id: "input", label: "Input" },
  { id: "output", label: "Output" },
  { id: "thinking", label: "Thinking" },
];

function detailValue(record: TrajectoryRecord, tab: DetailTab): string {
  const value = record[tab];
  return value?.trim() ? value : `(no ${tab} for this record)`;
}

export function TrajectoryInspector({
  record,
  className,
}: {
  record: TrajectoryRecord | null;
  className?: string;
}) {
  const [tab, setTab] = useState<DetailTab>("output");

  useEffect(() => {
    if (!record) return;
    setTab(record.kind === "user" ? "input" : record.thinking ? "thinking" : "output");
  }, [record?.id]);

  if (!record) {
    return (
      <aside
        className={cn(
          "bg-muted/20 flex min-h-0 flex-col border-l",
          className,
        )}
        data-testid="trajectory-inspector-empty"
      >
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
          <p className="text-sm font-medium">Select a record</p>
          <p className="text-muted-foreground max-w-xs text-xs">
            Choose a row to inspect its full input, output, or thinking content.
          </p>
        </div>
      </aside>
    );
  }

  return (
    <aside
      className={cn("bg-muted/20 flex min-h-0 flex-col border-l", className)}
      aria-label="Trajectory record inspector"
      data-testid="trajectory-inspector"
    >
      <div className="flex shrink-0 flex-col gap-3 border-b p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">{record.title}</p>
            <p className="text-muted-foreground mt-1 text-xs">
              #{record.index} · {formatKind(record.kind)} · turn {record.turn}
              {record.request ? ` · request ${record.request}` : ""}
            </p>
          </div>
          {record.isError && <Badge variant="destructive">error</Badge>}
        </div>
        <div className="text-muted-foreground flex flex-wrap gap-x-3 gap-y-1 text-xs">
          <span>{formatTimestamp(record.timestamp)}</span>
          <span>+{formatDuration(record.durationSeconds)}</span>
        </div>
        <div
          className="bg-muted flex rounded-md p-0.5"
          role="tablist"
          aria-label="Record details"
        >
          {DETAIL_TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              className={cn(
                "flex-1 rounded-sm px-2 py-1.5 text-xs font-medium transition-colors",
                tab === item.id
                  ? "bg-background text-foreground shadow-xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
              onClick={() => setTab(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <pre className="whitespace-pre-wrap break-words p-4 font-mono text-xs leading-relaxed">
          {detailValue(record, tab)}
        </pre>
      </ScrollArea>
    </aside>
  );
}
