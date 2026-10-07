"use client";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useMessageTiming } from "@assistant-ui/react";
import type { FC } from "react";

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 10_000) return `${(ms / 1000).toFixed(2)}s`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes}m ${Math.round((ms % 60_000) / 1000)}s`;
}

/**
 * Total response time on an assistant message, with the stream's speed in a
 * tooltip. Every row comes from `message.metadata.timing` (recorded off the
 * engine stream in `turn-timing.ts`); a row the runtime could not measure is
 * left out, and a message with no timing renders nothing.
 */
export const MessageTimingBadge: FC = () => {
  const timing = useMessageTiming();
  if (timing?.totalStreamTime === undefined) return null;

  const rows: Array<[label: string, value: string]> = [];
  if (timing.firstTokenTime !== undefined) {
    rows.push(["First Token", formatDuration(timing.firstTokenTime)]);
  }
  rows.push(["Total", formatDuration(timing.totalStreamTime)]);
  if (timing.tokensPerSecond !== undefined) {
    rows.push(["Speed", `${timing.tokensPerSecond.toFixed(1)} tok/s`]);
  }
  if (timing.totalChunks > 0) {
    rows.push(["Chunks", String(timing.totalChunks)]);
  }

  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label="Response timing"
            className="aui-message-timing text-muted-foreground hover:bg-accent hover:text-accent-foreground ms-1 h-6 rounded-md px-1.5 font-mono text-xs tabular-nums transition-colors"
          >
            {formatDuration(timing.totalStreamTime)}
          </button>
        </TooltipTrigger>
        <TooltipContent side="top">
          <dl className="grid min-w-36 gap-1">
            {rows.map(([label, value]) => (
              <div key={label} className="flex justify-between gap-4">
                <dt className="text-background/60">{label}</dt>
                <dd className="font-mono tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};
