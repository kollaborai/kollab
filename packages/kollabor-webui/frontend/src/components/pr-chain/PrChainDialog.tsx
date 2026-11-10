import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ExternalLink, Loader2, RefreshCw, X } from "lucide-react";
import type { EngineApi, PrChainSnapshot } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { describeEvent, itemUrl, shortTime, sortItems, statusLabel, statusTone, type StatusTone } from "./prChain";

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** While the dialog is open the table re-reads this often. */
const REFRESH_MS = 10_000;

const TONE: Record<StatusTone, string> = {
  failed: "text-destructive",
  attention: "text-highlight",
  active: "text-foreground",
  idle: "text-foreground",
  done: "text-muted-foreground",
};

type Props = { api: EngineApi; open: boolean; onOpenChange: (open: boolean) => void };

/** Read-only view of the pr-chain workflow: every PR and issue it tracks, and what it did last. */
export function PrChainDialog({ api, open, onOpenChange }: Props) {
  const [snapshot, setSnapshot] = useState<PrChainSnapshot | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);

  const load = useCallback((manual = false) => {
    const mine = epoch.current;
    if (manual) setBusy(true);
    api
      .getPrChain()
      .then((next) => {
        if (mine !== epoch.current) return;
        setSnapshot(next);
        setLoadError(null);
      })
      .catch((error: unknown) => {
        if (mine === epoch.current) setLoadError(errorText(error));
      })
      .finally(() => {
        if (mine === epoch.current) setBusy(false);
      });
  }, [api]);

  useEffect(() => {
    if (!open) return;
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      epoch.current += 1;
      window.clearInterval(timer);
    };
  }, [open, load]);

  const items = snapshot ? sortItems(snapshot.items) : [];
  const events = snapshot ? [...snapshot.events].reverse().slice(0, 12) : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        onOpenAutoFocus={(event) => event.preventDefault()}
        className="flex max-h-[min(40rem,90dvh)] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-[min(52rem,calc(100%-2rem))]"
      >
        <header className="flex items-start gap-3 px-4 pt-4 pb-3 sm:px-6">
          <div className="min-w-0 flex-1">
            <DialogTitle className="text-base font-semibold">PR Chain</DialogTitle>
            <DialogDescription className="text-muted-foreground text-sm">
              Every PR and issue the review workflow is working on.
            </DialogDescription>
          </div>
          <Button type="button" size="icon-sm" variant="ghost" aria-label="Refresh" className="shrink-0 max-sm:size-11" disabled={busy} onClick={() => load(true)}>
            {busy ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          </Button>
          <DialogClose asChild>
            <Button type="button" size="icon-sm" variant="ghost" aria-label="Close" className="shrink-0 max-sm:size-11">
              <X />
            </Button>
          </DialogClose>
        </header>

        {loadError ? (
          <div role="alert" className="bg-destructive/10 text-destructive mx-4 mb-2 flex items-start gap-2 rounded-md px-3 py-2 text-sm sm:mx-6">
            <AlertCircle className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0 flex-1 break-words" title={loadError}>
              Couldn&apos;t load the PR chain. Press Refresh to try again.
            </span>
          </div>
        ) : null}

        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 sm:px-6">
          {snapshot === null ? (
            <div className="space-y-2" aria-label="Loading">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : !snapshot.root_found || items.length === 0 ? (
            <p className="text-muted-foreground py-8 text-center text-sm">
              {snapshot.root_found ? "Nothing is tracked yet. " : "No PR chain state on this machine. "}
              Run <code className="text-foreground font-mono text-xs">pr-chain run &lt;pr number&gt;</code> or{" "}
              <code className="text-foreground font-mono text-xs">pr-chain issue &lt;issue number&gt;</code> and it shows up here.
            </p>
          ) : (
            <>
              <ul className="divide-border divide-y" aria-label="Tracked items">
                {items.map((item) => {
                  const tone = statusTone(item);
                  return (
                    <li key={`${item.kind}-${item.number}`} className="grid grid-cols-[auto_1fr] items-baseline sm:grid-cols-[auto_1fr_auto] gap-x-3 gap-y-0.5 py-2 text-sm">
                      <a
                        href={itemUrl(item)}
                        target="_blank"
                        rel="noreferrer"
                        className="hover:text-primary flex items-center gap-1 font-mono text-xs"
                      >
                        {item.kind === "pr" ? "PR" : "Issue"} {item.number}
                        <ExternalLink className="size-3" />
                      </a>
                      <span className={cn("min-w-0 break-words font-medium", TONE[tone])}>
                        {statusLabel(item.status)}
                        {item.claimed ? <span className="text-muted-foreground ml-1.5 font-normal">Running</span> : null}
                      </span>
                      <span className="text-muted-foreground col-span-2 font-mono text-xs sm:col-span-1 sm:whitespace-nowrap">
                        {item.round ? `Round ${item.round}` : ""}
                        {item.round && item.updated ? " · " : ""}
                        {shortTime(item.updated)}
                      </span>
                      {item.why || item.last_verdict ? (
                        <span className="text-muted-foreground col-span-2 min-w-0 break-words text-xs sm:col-span-3">
                          {[item.last_verdict, item.why].filter(Boolean).join(" · ")}
                        </span>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
              {events.length ? (
                <section className="mt-4">
                  <h3 className="text-muted-foreground mb-1 text-xs font-medium">Recent activity</h3>
                  <ul className="space-y-0.5">
                    {events.map((event, index) => (
                      <li key={`${event.ts}-${index}`} className="flex gap-3 text-xs">
                        <span className="text-muted-foreground shrink-0 font-mono">{shortTime(event.ts)}</span>
                        <span className="min-w-0 truncate">{describeEvent(event)}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
