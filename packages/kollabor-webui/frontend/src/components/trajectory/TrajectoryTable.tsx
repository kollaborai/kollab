import type { KeyboardEvent } from "react";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { TrajectoryRecord } from "./trajectory-records";
import { formatDuration, formatKind } from "./trajectory-format";

function kindClass(kind: TrajectoryRecord["kind"]): string {
  switch (kind) {
    case "user":
      return "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-200";
    case "assistant":
      return "border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-900 dark:bg-violet-950 dark:text-violet-200";
    case "tool":
    case "tool-batch":
      return "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200";
    case "system":
      return "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-200";
    default:
      return "";
  }
}

export function TrajectoryTable({
  records,
  selectedId,
  loading,
  onSelect,
}: {
  records: TrajectoryRecord[];
  selectedId: string | null;
  loading: boolean;
  onSelect: (id: string | null) => void;
}) {
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onSelect(null);
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    if (!records.length) return;
    const currentIndex = records.findIndex((record) => record.id === selectedId);
    const nextIndex =
      event.key === "ArrowDown"
        ? currentIndex < records.length - 1
          ? currentIndex + 1
          : 0
        : currentIndex > 0
          ? currentIndex - 1
          : records.length - 1;
    onSelect(records[nextIndex]?.id || null);
  };

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      data-testid="trajectory-ledger"
    >
      <div className="bg-muted/30 text-muted-foreground grid shrink-0 grid-cols-[3rem_9rem_minmax(0,1fr)] border-b px-3 py-2 text-[11px] font-semibold tracking-wide uppercase md:grid-cols-[4rem_12rem_minmax(0,1fr)]">
        <span>#</span>
        <span>Event</span>
        <span>Content</span>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <div
          role="table"
          aria-label="Session trajectory"
          tabIndex={0}
          onKeyDown={handleKeyDown}
          className="min-h-full outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-inset"
        >
          {loading ? (
            <div className="space-y-1 p-2" aria-label="Loading trajectory">
              {Array.from({ length: 7 }, (_, index) => (
                <div
                  key={index}
                  className="bg-muted h-10 animate-pulse rounded-md"
                />
              ))}
            </div>
          ) : records.length ? (
            records.map((record) => (
              <button
                type="button"
                key={record.id}
                aria-selected={selectedId === record.id}
                data-record-id={record.id}
                className={cn(
                  "grid w-full cursor-pointer grid-cols-[3rem_9rem_minmax(0,1fr)] items-center border-b px-3 py-2 text-left text-sm transition-colors md:grid-cols-[4rem_12rem_minmax(0,1fr)]",
                  record.opensTurn && "border-t-2 border-t-primary/40",
                  record.kind === "tool" || record.kind === "tool-batch"
                    ? "bg-muted/10"
                    : "",
                  selectedId === record.id
                    ? "bg-accent text-accent-foreground"
                    : "hover:bg-accent/50",
                )}
                onClick={() => onSelect(record.id)}
              >
                <span className="text-muted-foreground font-mono text-xs">
                  {record.index}
                </span>
                <span className="flex min-w-0 flex-wrap items-center gap-1 pr-2">
                  <Badge
                    variant="outline"
                    className={cn(
                      "max-w-full truncate text-[10px]",
                      kindClass(record.kind),
                    )}
                  >
                    {formatKind(record.kind)}
                  </Badge>
                  {record.turn > 0 && (
                    <span className="text-muted-foreground text-[10px]">
                      T{record.turn}
                    </span>
                  )}
                  {record.request !== null && (
                    <span className="text-muted-foreground text-[10px]">
                      R{record.request}
                    </span>
                  )}
                </span>
                <span
                  className={cn(
                    "flex min-w-0 items-center gap-2",
                    (record.kind === "tool" || record.kind === "tool-batch") &&
                      "pl-3",
                  )}
                >
                  <span
                    className={cn(
                      "min-w-0 flex-1 truncate",
                      record.isError && "text-destructive",
                    )}
                    title={record.summary}
                  >
                    {record.summary}
                  </span>
                  <span className="text-muted-foreground shrink-0 font-mono text-[10px]">
                    +{formatDuration(record.durationSeconds)}
                  </span>
                </span>
              </button>
            ))
          ) : (
            <div className="text-muted-foreground flex min-h-48 items-center justify-center p-6 text-center text-sm">
              No trajectory records match this view.
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
