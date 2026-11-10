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
import { describeEvent, itemUrl, shortTime, sortItems, statusTone, type StatusTone } from "./prChain";

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** While the dialog is open the table re-reads this often. */
const REFRESH_MS = 10_000;

const TONE: Record<StatusTone, string> = {
  attention: "text-amber-700 dark:text-amber-300",
  active: "text-emerald-700 dark:text-emerald-300",
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

  const load = useCallback(() => {
    const mine = epoch.current;
    setBusy(true);
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
        className="flex max-h-[min(40rem,90dvh)] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-[min(52rem,calc(100%-2rem))]"
      >
        <header className="flex items-start gap-3 px-4 pt-4 pb-3 sm:px-6">
          <div className="min-w-0 flex-1">
            <DialogTitle className="text-base font-semibold">PR Chain</DialogTitle>
            <DialogDescription className="text-muted-foreground text-sm">
              Every PR and issue the review workflow is working on.
            </DialogDescription>
          </div>
          <Button type="button" size="icon-xs" variant="ghost" aria-label="Refresh" disabled={busy} onClick={load}>
            {busy ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          </Button>
          <DialogClose className="text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-ring/50 -mr-1.5 shrink-0 rounded-full p-1.5 outline-none focus-visible:ring-[3px]">
            <X className="size-4" />
            <span className="sr-only">Close</span>
          </DialogClose>
        </header>

        {loadError ? (
          <div role="alert" className="bg-destructive/10 text-destructive mx-4 mb-2 flex items-start gap-2 rounded-md px-3 py-2 text-sm sm:mx-6">
            <AlertCircle className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0 flex-1 break-words">{loadError}</span>
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
              {snapshot.root_found
                ? "Nothing is tracked yet. Run pr-chain on a PR or an issue and it shows up here."
                : "No pr-chain state on this machine. Run pr-chain once and it shows up here."}
            </p>
          ) : (
            <>
              <ul className="divide-border divide-y" aria-label="Tracked items">
                {items.map((item) => {
                  const tone = statusTone(item);
                  return (
                    <li key={`${item.kind}-${item.number}`} className="grid grid-cols-[auto_1fr_auto] items-baseline gap-x-3 gap-y-0.5 py-2 text-sm">
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
                        {item.status ?? "Unknown"}
                        {item.claimed ? " (running)" : ""}
                      </span>
                      <span className="text-muted-foreground font-mono text-xs whitespace-nowrap">
                        {item.round ? `Round ${item.round}` : ""}
                        {item.round && item.updated ? " · " : ""}
                        {shortTime(item.updated)}
                      </span>
                      {item.why || item.last_verdict ? (
                        <span className="text-muted-foreground col-span-3 line-clamp-2 min-w-0 text-xs">
                          {[item.last_verdict, item.why].filter(Boolean).join(" · ")}
                        </span>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
              {events.length ? (
                <section className="mt-4">
                  <h3 className="text-muted-foreground mb-1 text-xs font-semibold uppercase">Recent Activity</h3>
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
