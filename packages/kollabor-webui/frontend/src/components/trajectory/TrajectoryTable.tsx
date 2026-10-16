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
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { TrajectoryRecord } from "./trajectory-records";
import { formatDuration, formatKind, formatTokens } from "./trajectory-format";

function kindClass(kind: TrajectoryRecord["kind"]): string {
  switch (kind) {
    case "user":
      return "text-blue-700 dark:text-blue-300";
    case "assistant":
      return "text-violet-700 dark:text-violet-300";
    case "tool":
    case "tool-batch":
      return "text-amber-700 dark:text-amber-300";
    case "system":
      return "text-slate-700 dark:text-slate-300";
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

function RecordSummary({ record }: { record: TrajectoryRecord }) {
  const separator = record.kind === "tool" || record.kind === "tool-batch"
    ? " → "
    : null;
  const [label, detail] = separator
    ? record.summary.split(separator, 2)
    : [record.summary, undefined];

  if (!detail) {
    return <span className="min-w-0 truncate">{record.summary}</span>;
  }

  return (
    <span className="flex min-w-0 items-center gap-1.5 truncate">
      {/* A long output must not squeeze the tool name ("Run ls" showed "R…"). */}
      <span className="max-w-[50%] shrink-0 truncate font-medium text-foreground/90">{label}</span>
      <span className="text-muted-foreground/60 shrink-0">→</span>
      <span className="text-muted-foreground min-w-0 flex-1 truncate">{detail}</span>
    </span>
  );
}

export function LoadEarlierBanner({
  loading,
  onLoad,
}: {
  loading: boolean;
  onLoad: () => void;
}) {
  return (
    <div className="bg-primary/[0.06] flex shrink-0 items-center justify-between gap-3 px-3 py-2">
      <span className="text-muted-foreground flex min-w-0 items-center gap-2 text-xs">
        <History className="text-primary size-3.5 shrink-0" />
        <span>Older records are available.</span>
      </span>
      <button
        type="button"
        className="text-primary shrink-0 rounded-md px-2 py-1 text-xs font-medium underline-offset-4 hover:bg-primary/10 hover:underline disabled:opacity-50"
        onClick={onLoad}
        disabled={loading}
      >
        {loading ? "Loading…" : "Load Earlier"}
      </button>
    </div>
  );
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
        <LoadEarlierBanner loading={loadingEarlier} onLoad={onLoadEarlier} />
      )}
      <div className="bg-muted/40 text-muted-foreground grid shrink-0 grid-cols-[2.75rem_minmax(8.5rem,10rem)_minmax(0,1fr)] px-3 py-2.5 text-[0.625rem] font-semibold tracking-[0.14em] uppercase md:grid-cols-[3.5rem_minmax(10rem,11rem)_minmax(0,1fr)]">
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
                    "group grid min-h-10 w-full cursor-pointer grid-cols-[2.75rem_minmax(8.5rem,10rem)_minmax(0,1fr)] items-center px-3 py-2 text-left text-sm transition-colors focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60 md:grid-cols-[3.5rem_minmax(10rem,11rem)_minmax(0,1fr)]",
                    // Turns alternate shade; tools inside one carry an amber tint.
                    record.turn % 2 === 1 && "bg-muted/[0.16]",
                    (record.kind === "tool" || record.kind === "tool-batch") && "bg-amber-500/[0.05]",
                    selectedId === record.id
                      ? "bg-primary/[0.1] text-accent-foreground"
                      : "hover:bg-accent/45",
                  )}
                  onClick={() => onSelect(record.id)}
                >
                  <span className="text-muted-foreground tabular-nums font-mono text-[0.6875rem]">
                    {record.index}
                  </span>
                  <span className="flex min-w-0 flex-wrap items-center gap-1 pr-2">
                    <span
                      className={cn(
                        "flex max-w-full items-center gap-1 text-[0.6875rem] font-medium",
                        kindClass(record.kind),
                      )}
                    >
                      <Icon className="size-3 shrink-0" />
                      <span className="truncate">
                        {formatKind(record.kind)}
                      </span>
                    </span>
                    {record.turn > 0 && (
                      <span className="text-muted-foreground font-mono text-[0.625rem]">
                        T{record.turn}
                      </span>
                    )}
                    {record.request !== null && (
                      <span className="text-muted-foreground font-mono text-[0.625rem]">
                        R{record.request}
                      </span>
                    )}
                  </span>
                  <span
                    className={cn(
                      "flex min-w-0 items-center gap-2 leading-5",
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
                      <RecordSummary record={record} />
                    </span>
                    {(record.inputTokens !== undefined ||
                      record.outputTokens !== undefined) && (
                      <span className="text-muted-foreground hidden shrink-0 font-mono text-[0.625rem] xl:inline">
                        in {formatTokens(record.inputTokens)} · out {formatTokens(record.outputTokens)}
                      </span>
                    )}
                    <span className="text-muted-foreground shrink-0 tabular-nums font-mono text-[0.625rem]">
                      {formatDuration(record.durationSeconds)}
                    </span>
                  </span>
                </button>
              );
            })
          ) : (
            <div className="text-muted-foreground flex min-h-56 flex-col items-center justify-center gap-3 p-6 text-center">
              <div className="text-muted-foreground flex size-10 items-center justify-center">
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
