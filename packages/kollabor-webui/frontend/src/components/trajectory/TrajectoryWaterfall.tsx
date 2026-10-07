import {
  createContext,
  useContext,
  useMemo,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { ChevronRight, Clock } from "lucide-react";
import { AuiProvider, useAui, useAuiState } from "@assistant-ui/react";
import { SpanPrimitive, SpanResource } from "@assistant-ui/react-o11y";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatDuration, formatKind } from "./trajectory-format";
import type { TrajectoryRecord } from "./trajectory-records";
import { traceSpans, type Trace } from "./trajectory-spans";
import { LoadEarlierBanner } from "./TrajectoryTable";

type RowContextValue = {
  trace: Trace;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
};

const RowContext = createContext<RowContextValue | null>(null);

const GRID =
  "grid grid-cols-[minmax(0,1fr)_4rem_minmax(4.5rem,28%)] items-center gap-2 sm:gap-3 md:grid-cols-[minmax(0,1fr)_4.5rem_minmax(8rem,36%)]";

function SpanRow() {
  const { trace, selectedId, onSelect } = useContext(RowContext)!;
  const id = useAuiState((s) => s.span.id);
  const type = useAuiState((s) => s.span.type);
  const latencyMs = useAuiState((s) => s.span.latencyMs);
  const selectable = trace.recordIds.has(id);
  const select = () => {
    if (selectable) onSelect(id);
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    select();
  };

  return (
    <SpanPrimitive.Root
      role="row"
      tabIndex={selectable ? 0 : -1}
      aria-selected={selectedId === id}
      onClick={select}
      onKeyDown={handleKeyDown}
      className={`${GRID} group min-h-9 cursor-pointer border-b border-border/45 pr-3 text-sm transition-colors outline-none hover:bg-accent/45 focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-inset aria-selected:bg-primary/[0.08] data-[span-type=turn]:border-t-2 data-[span-type=turn]:border-t-primary/40 data-[span-type=turn]:font-medium`}
    >
      <SpanPrimitive.Indent
        baseIndent={10}
        indentPerLevel={16}
        className="flex min-w-0 items-center gap-1.5"
      >
        <span className="flex size-4 shrink-0 items-center justify-center">
          <SpanPrimitive.CollapseToggle
            aria-label="Toggle children"
            className="text-muted-foreground hover:text-foreground flex size-4 items-center justify-center rounded-sm [&[data-collapsed=false]>svg]:rotate-90"
          >
            <ChevronRight className="size-3.5 transition-transform" />
          </SpanPrimitive.CollapseToggle>
        </span>
        <SpanPrimitive.StatusIndicator className="size-1.5 shrink-0 rounded-full bg-emerald-500/80 data-[span-status=failed]:bg-destructive data-[span-status=running]:animate-pulse data-[span-status=running]:bg-amber-400" />
        <SpanPrimitive.TypeBadge className="text-muted-foreground hidden shrink-0 rounded bg-muted/60 px-1 py-0.5 font-mono text-[10px] sm:inline">
          {type === "turn" ? "Turn" : formatKind(type)}
        </SpanPrimitive.TypeBadge>
        <SpanPrimitive.Name className="min-w-0 truncate group-data-[span-status=failed]:text-destructive" />
      </SpanPrimitive.Indent>
      <span className="text-muted-foreground text-right font-mono text-[10px] tabular-nums">
        {formatDuration(latencyMs === null ? null : latencyMs / 1000)}
      </span>
      <div className="relative h-5">
        <span className="absolute inset-x-0 top-1/2 h-px bg-border/60" />
        <SpanPrimitive.TimelineBar
          timeRange={trace.ranges.get(id)}
          className="top-1/2 h-2 -translate-y-1/2 rounded-full bg-violet-500/70 [--span-timeline-min-width:3px] data-[span-status=failed]:bg-destructive/80 data-[span-type=tool]:bg-amber-500/75 data-[span-type=tool-batch]:bg-amber-500/75 data-[span-type=turn]:bg-primary/35 data-[span-type=system]:bg-slate-400/60 data-[span-type=message]:bg-slate-400/60"
        />
      </div>
    </SpanPrimitive.Root>
  );
}

function TraceProvider({
  spans,
  children,
}: {
  spans: Trace["spans"];
  children: ReactNode;
}) {
  const aui = useAui({ span: SpanResource({ spans }) });
  return <AuiProvider value={aui}>{children}</AuiProvider>;
}

/**
 * The trajectory as a trace waterfall: one block per turn, model requests
 * under the prompt and tool calls under their request, each turn scaled to
 * its own window. Built on assistant-ui's o11y span primitives.
 */
export function TrajectoryWaterfall({
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
  const trace = useMemo(() => traceSpans(records), [records]);
  const row = useMemo(
    () => ({ trace, selectedId, onSelect }),
    [trace, selectedId, onSelect],
  );

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background/30"
      data-testid="trajectory-waterfall"
    >
      {canLoadEarlier && (
        <LoadEarlierBanner loading={loadingEarlier} onLoad={onLoadEarlier} />
      )}
      <div
        className={`${GRID} bg-muted/55 text-muted-foreground shrink-0 border-b py-2.5 pr-3 pl-3 text-[10px] font-semibold tracking-[0.1em] uppercase sm:tracking-[0.14em] backdrop-blur`}
      >
        <span>Span</span>
        <span className="text-right">Duration</span>
        <span>Timeline</span>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        {loading ? (
          <div className="space-y-1 p-2" aria-label="Loading trajectory">
            {Array.from({ length: 7 }, (_, index) => (
              <div key={index} className="bg-muted/70 h-9 animate-pulse rounded-lg" />
            ))}
          </div>
        ) : trace.spans.length ? (
          <div role="table" aria-label="Session trace waterfall">
            <RowContext.Provider value={row}>
              <TraceProvider spans={trace.spans}>
                <SpanPrimitive.Children components={{ Span: SpanRow }} />
              </TraceProvider>
            </RowContext.Provider>
            {trace.untimed > 0 && (
              <p className="text-muted-foreground px-3 py-2 text-xs">
                {trace.untimed} {trace.untimed === 1 ? "record has" : "records have"} no
                timestamp and {trace.untimed === 1 ? "is" : "are"} not shown.
              </p>
            )}
          </div>
        ) : (
          <div className="text-muted-foreground flex min-h-56 flex-col items-center justify-center gap-3 p-6 text-center">
            <div className="bg-muted/70 text-muted-foreground flex size-10 items-center justify-center rounded-xl">
              <Clock className="size-5" />
            </div>
            <div>
              <p className="text-foreground text-sm font-medium">
                {records.length
                  ? "These records carry no timing data."
                  : "No trajectory records match this view."}
              </p>
              <p className="mt-1 text-xs">
                {records.length
                  ? "Use the table to read them."
                  : "Try a different search or load earlier records."}
              </p>
            </div>
          </div>
        )}
      </ScrollArea>
    </div>
  );
}
