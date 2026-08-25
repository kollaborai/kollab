import type { KeyboardEvent } from "react";
import {
  Bot,
  CircleAlert,
  History,
  Layers3,
  MessageSquare,
  SearchX,
  Settings2,
  UserRound,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { TrajectoryRecord } from "./trajectory-records";
import { formatDuration, formatKind, formatTokens } from "./trajectory-format";

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

function kindIcon(kind: TrajectoryRecord["kind"]): LucideIcon {
  switch (kind) {
    case "user":
      return UserRound;
    case "assistant":
      return Bot;
    case "tool":
      return Wrench;
    case "tool-batch":
      return Layers3;
    case "system":
      return Settings2;
    default:
      return MessageSquare;
  }
}

export function TrajectoryTable({
  records,
  selectedId,
  loading,
  canLoadEarlier,
  loadingEarlier,
  onLoadEarlier,
  onSelect,
}: {
  records: TrajectoryRecord[];
  selectedId: string | null;
  loading: boolean;
  canLoadEarlier: boolean;
  loadingEarlier: boolean;
  onLoadEarlier: () => void;
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
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background/30"
      data-testid="trajectory-ledger"
    >
      {canLoadEarlier && (
        <div className="border-primary/15 bg-primary/[0.04] flex shrink-0 items-center justify-between gap-3 border-b px-3 py-2">
          <span className="text-muted-foreground flex min-w-0 items-center gap-2 text-xs">
            <History className="text-primary size-3.5 shrink-0" />
            <span>Older records are available.</span>
          </span>
          <button
            type="button"
            className="text-primary shrink-0 rounded-md px-2 py-1 text-xs font-medium underline-offset-4 hover:bg-primary/10 hover:underline disabled:opacity-50"
            onClick={onLoadEarlier}
            disabled={loadingEarlier}
          >
            {loadingEarlier ? "Loading…" : "Load earlier"}
          </button>
        </div>
      )}
      <div className="bg-muted/40 text-muted-foreground grid shrink-0 grid-cols-[3rem_9rem_minmax(0,1fr)] border-b px-3 py-2.5 text-[10px] font-semibold tracking-[0.14em] uppercase md:grid-cols-[4rem_12rem_minmax(0,1fr)]">
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
                  className="bg-muted/70 h-10 animate-pulse rounded-lg"
                />
              ))}
            </div>
          ) : records.length ? (
            records.map((record) => {
              const Icon = kindIcon(record.kind);
              return (
                <button
                  type="button"
                  key={record.id}
                  aria-selected={selectedId === record.id}
                  data-record-id={record.id}
                  className={cn(
                    "grid min-h-11 w-full cursor-pointer grid-cols-[3rem_9rem_minmax(0,1fr)] items-center border-b border-border/50 px-3 py-2.5 text-left text-sm transition-colors md:grid-cols-[4rem_12rem_minmax(0,1fr)]",
                    record.opensTurn && "border-t-2 border-t-primary/40",
                    record.kind === "tool" || record.kind === "tool-batch"
                      ? "border-l-2 border-l-amber-500/60 bg-amber-500/[0.035]"
                      : "",
                    selectedId === record.id
                      ? "bg-accent text-accent-foreground shadow-[inset_0_1px_0_hsl(var(--primary)/0.2),inset_0_-1px_0_hsl(var(--primary)/0.2)]"
                      : "hover:bg-accent/45",
                  )}
                  onClick={() => onSelect(record.id)}
                >
                  <span className="text-muted-foreground tabular-nums font-mono text-[11px]">
                    {record.index}
                  </span>
                  <span className="flex min-w-0 flex-wrap items-center gap-1 pr-2">
                    <Badge
                      variant="outline"
                      className={cn(
                        "max-w-full gap-1 truncate text-[10px]",
                        kindClass(record.kind),
                      )}
                    >
                      <Icon className="size-3 shrink-0" />
                      <span className="truncate">
                        {formatKind(record.kind)}
                      </span>
                    </Badge>
                    {record.turn > 0 && (
                      <span className="text-muted-foreground rounded bg-muted/60 px-1 py-0.5 font-mono text-[10px]">
                        T{record.turn}
                      </span>
                    )}
                    {record.request !== null && (
                      <span className="text-muted-foreground rounded bg-muted/60 px-1 py-0.5 font-mono text-[10px]">
                        R{record.request}
                      </span>
                    )}
                  </span>
                  <span
                    className={cn(
                      "flex min-w-0 items-center gap-2",
                      (record.kind === "tool" || record.kind === "tool-batch") &&
                        "pl-2",
                    )}
                  >
                    {record.isError && (
                      <CircleAlert className="text-destructive size-3.5 shrink-0" />
                    )}
                    <span
                      className={cn(
                        "min-w-0 flex-1 truncate",
                        record.isError && "text-destructive",
                      )}
                      title={record.summary}
                    >
                      {record.summary}
                    </span>
                    {(record.inputTokens !== undefined ||
                      record.outputTokens !== undefined) && (
                      <span className="text-muted-foreground hidden shrink-0 font-mono text-[10px] lg:inline">
                        in {formatTokens(record.inputTokens)} · out {formatTokens(record.outputTokens)}
                      </span>
                    )}
                    <span className="text-muted-foreground shrink-0 tabular-nums font-mono text-[10px]">
                      {formatDuration(record.durationSeconds)}
                    </span>
                  </span>
                </button>
              );
            })
          ) : (
            <div className="text-muted-foreground flex min-h-56 flex-col items-center justify-center gap-3 p-6 text-center">
              <div className="bg-muted/70 text-muted-foreground flex size-10 items-center justify-center rounded-xl">
                <SearchX className="size-5" />
              </div>
              <div>
                <p className="text-foreground text-sm font-medium">
                  No trajectory records match this view.
                </p>
                <p className="mt-1 text-xs">
                  Try a different search or load earlier records.
                </p>
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
