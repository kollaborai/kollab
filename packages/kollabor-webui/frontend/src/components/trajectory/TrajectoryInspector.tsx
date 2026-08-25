import { useEffect, useState } from "react";
import {
  BrainCircuit,
  CircleAlert,
  FileInput,
  FileOutput,
  PanelRight,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
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
  return value?.trim()
    ? formatContent(value)
    : `(no ${tab} for this record)`;
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
          "bg-gradient-to-b from-muted/30 to-background/10 flex min-h-0 flex-col border-l",
          className,
        )}
        data-testid="trajectory-inspector-empty"
      >
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <div className="bg-primary/10 text-primary flex size-11 items-center justify-center rounded-2xl">
            <PanelRight className="size-5" />
          </div>
          <div>
            <p className="text-sm font-semibold">Inspect a record</p>
            <p className="text-muted-foreground mt-1 max-w-xs text-xs leading-relaxed">
              Choose a row to inspect its full input, output, or thinking content.
            </p>
          </div>
        </div>
      </aside>
    );
  }

  return (
    <aside
      className={cn(
        "bg-gradient-to-b from-muted/30 to-background/10 flex min-h-0 flex-col border-l",
        className,
      )}
      aria-label="Trajectory record inspector"
      data-testid="trajectory-inspector"
    >
      <div className="bg-muted/10 flex shrink-0 flex-col gap-3 border-b p-4">
        <div className="flex items-start gap-3">
          <div className="bg-primary/10 text-primary flex size-8 shrink-0 items-center justify-center rounded-lg">
            <PanelRight className="size-4" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold">{record.title}</p>
            <p className="text-muted-foreground mt-1 text-[11px]">
              #{record.index} · {formatKind(record.kind)} · turn {record.turn}
              {record.request ? ` · request ${record.request}` : ""}
            </p>
          </div>
          {record.isError && (
            <Badge variant="destructive" className="gap-1">
              <CircleAlert className="size-3" />
              error
            </Badge>
          )}
        </div>
        <div className="text-muted-foreground flex flex-wrap gap-1.5 text-[11px]">
          <span className="rounded-md bg-background/60 px-2 py-1">
            {formatTimestamp(record.timestamp)}
          </span>
          <span className="rounded-md bg-background/60 px-2 py-1">
            {formatDuration(record.durationSeconds)}
          </span>
          {(record.inputTokens !== undefined ||
            record.outputTokens !== undefined) && (
            <span className="rounded-md bg-background/60 px-2 py-1">
              in {formatTokens(record.inputTokens)} · out {formatTokens(record.outputTokens)}
            </span>
          )}
        </div>
        <div
          className="bg-muted/70 flex rounded-lg p-1"
          role="tablist"
          aria-label="Record details"
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
                  "flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors",
                  tab === item.id
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
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
        <pre className="bg-background/20 whitespace-pre-wrap break-words p-4 font-mono text-[11px] leading-relaxed">
          {detailValue(record, tab)}
        </pre>
      </ScrollArea>
    </aside>
  );
}
