import { useEffect, useState } from "react";
import {
  ArrowDownUp,
  BrainCircuit,
  CircleAlert,
  FileInput,
  FileOutput,
  PanelRight,
  type LucideIcon,
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { TrajectoryRecord } from "./trajectory-records";
import {
  formatDuration,
  formatKind,
  formatTimestamp,
  formatTokens,
} from "./trajectory-format";
import { cn } from "@/lib/utils";
import { formatContent } from "@/utils/format-content";

type DetailTab = "input" | "output" | "thinking";

const DETAIL_TABS: Array<{ id: DetailTab; label: string }> = [
  { id: "input", label: "Input" },
  { id: "output", label: "Output" },
  { id: "thinking", label: "Thinking" },
];

function detailIcon(tab: DetailTab): LucideIcon {
  switch (tab) {
    case "input":
      return FileInput;
    case "output":
      return FileOutput;
    default:
      return BrainCircuit;
  }
}

function detailValue(record: TrajectoryRecord, tab: DetailTab): string {
  const value = record[tab];
  return value?.trim() ? formatContent(value) : "";
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
          "bg-muted/25 flex min-h-0 flex-col",
          className,
        )}
        data-testid="trajectory-inspector-empty"
      >
        <div className="bg-muted/30 flex shrink-0 items-center gap-3 px-4 py-3">
          <div className="text-muted-foreground flex size-8 items-center justify-center">
            <PanelRight className="size-4" />
          </div>
          <div className="min-w-0">
            <p className="text-muted-foreground text-[0.625rem] font-semibold tracking-[0.14em] uppercase">
              Inspector
            </p>
            <p className="text-sm font-semibold">Record Details</p>
          </div>
        </div>
        <div className="flex flex-1 flex-col items-center justify-center p-6 text-center">
          <div className="w-full max-w-xs rounded-2xl bg-background/50 p-5">
            <div className="text-muted-foreground mx-auto flex size-11 items-center justify-center">
              <PanelRight className="size-5" />
            </div>
            <p className="mt-4 text-sm font-semibold">Inspect a Record</p>
            <p className="text-muted-foreground mt-1 text-xs leading-relaxed">
              Choose a row to inspect its full input, output, or thinking content.
            </p>
            <div className="text-muted-foreground mt-4 flex items-center justify-center gap-2 text-[0.625rem]">
              <span className="inline-flex items-center gap-1 rounded-md bg-muted/70 px-2 py-1 font-mono">
                <ArrowDownUp className="size-3" />
                ↑ ↓ navigate
              </span>
              <span className="rounded-md bg-muted/70 px-2 py-1 font-mono">
                Esc clear
              </span>
            </div>
          </div>
        </div>
      </aside>
    );
  }

  return (
    <aside
      className={cn(
        "bg-muted/25 flex min-h-0 flex-col",
        className,
      )}
      aria-label="Trajectory record inspector"
      data-testid="trajectory-inspector"
    >
      <div className="bg-muted/30 flex shrink-0 flex-col gap-3 p-4">
        <div className="flex items-start gap-3">
          <div className="text-muted-foreground flex size-8 shrink-0 items-center justify-center">
            <PanelRight className="size-4" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-muted-foreground text-[0.625rem] font-semibold tracking-[0.14em] uppercase">
              Record {record.index}
            </p>
            <p className="mt-1 line-clamp-2 break-words text-sm font-semibold">
              {record.summary}
            </p>
            <p className="text-muted-foreground mt-1 text-[0.6875rem]">
              {formatKind(record.kind)} · turn {record.turn}
              {record.request ? ` · request ${record.request}` : ""}
            </p>
          </div>
          {record.isError && (
            <span className="text-destructive flex shrink-0 items-center gap-1 text-xs font-medium">
              <CircleAlert className="size-3" />
              Error
            </span>
          )}
        </div>
        <div className="text-muted-foreground flex flex-wrap gap-x-3 gap-y-1 text-[0.6875rem] tabular-nums">
          <span>
            {formatTimestamp(record.timestamp)}
          </span>
          <span>
            {formatDuration(record.durationSeconds)}
          </span>
          {(record.inputTokens !== undefined ||
            record.outputTokens !== undefined) && (
            <span>
              in {formatTokens(record.inputTokens)} · out {formatTokens(record.outputTokens)}
            </span>
          )}
        </div>
        <div
          className="flex gap-1"
          role="tablist"
          aria-label="Record Details"
        >
          {DETAIL_TABS.map((item) => {
            const Icon = detailIcon(item.id);
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={tab === item.id}
                className={cn(
                  "flex-1 border-b-2 px-2 pt-1.5 pb-1 text-xs font-medium transition-colors",
                  tab === item.id
                    ? "border-foreground text-foreground"
                    : "text-muted-foreground hover:text-foreground border-transparent",
                )}
                onClick={() => setTab(item.id)}
              >
                <Icon className="mr-1.5 inline-block size-3.5 align-[-0.15em]" />
                {item.label}
              </button>
            );
          })}
        </div>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        {detailValue(record, tab) ? (
          <pre className="bg-background/20 whitespace-pre-wrap break-words p-4 font-mono text-[0.6875rem] leading-relaxed">
            {detailValue(record, tab)}
          </pre>
        ) : (
          <div className="p-4">
            <div className="rounded-xl bg-background/50 p-4">
              <p className="text-sm font-medium">No {tab} captured</p>
              <p className="text-muted-foreground mt-1 text-xs leading-relaxed">
                This record does not include {tab} content in session history.
              </p>
            </div>
          </div>
        )}
      </ScrollArea>
    </aside>
  );
}
