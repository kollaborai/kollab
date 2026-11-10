import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, RefreshCw, X } from "lucide-react";
import type { EngineApi, PrChainSnapshot } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { NoticeAction, NoticeBar } from "@/components/shell/NoticeBar";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { describeEvent, inFlight, inOrder, itemUrl, shortTime, sortItems, statusLabel, statusTone, type StatusTone } from "./prChain";

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
  const order = useRef(inOrder());
  const requests = useRef(inFlight());

  const load = useCallback((manual = false) => {
    const mine = epoch.current;
    const isCurrent = order.current();
    const { signal, end } = requests.current.begin();
    if (manual) setBusy(true);
    api
      .getPrChain(signal)
      .then((next) => {
        if (mine !== epoch.current || !isCurrent()) return;
        setSnapshot(next);
        setLoadError(null);
      })
      .catch((error: unknown) => {
        if (signal.aborted) return;
        if (mine === epoch.current && isCurrent()) setLoadError(errorText(error));
      })
      .finally(() => {
        end();
        if (mine === epoch.current && isCurrent()) setBusy(false);
      });
  }, [api]);

  useEffect(() => {
    if (!open) return;
    load();
    // A timed read waits for the previous one, so slow reads do not pile up on the engine.
    const timer = window.setInterval(() => {
      if (!requests.current.busy()) load();
    }, REFRESH_MS);
    return () => {
      epoch.current += 1;
      window.clearInterval(timer);
      requests.current.abortAll();
      setBusy(false);
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
          <Button type="button" size="icon-sm" variant="ghost" aria-label="Refresh" className="shrink-0 max-lg:size-11" disabled={busy} onClick={() => load(true)}>
            {busy ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          </Button>
          <DialogClose asChild>
            <Button type="button" size="icon-sm" variant="ghost" aria-label="Close" className="shrink-0 max-lg:size-11">
              <X />
            </Button>
          </DialogClose>
        </header>

        {loadError ? (
          <NoticeBar
            urgent
            detail={loadError}
            actions={
              <NoticeAction className="text-foreground max-lg:h-11" disabled={busy} onClick={() => load(true)}>
                Try again
              </NoticeAction>
            }
          >
            Couldn&apos;t load the PR Chain.
          </NoticeBar>
        ) : null}

        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 sm:px-6">
          {snapshot === null && loadError ? null : snapshot === null ? (
            <div className="space-y-2" role="status">
              <span className="sr-only">Loading PR Chain</span>
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : !snapshot.root_found || items.length === 0 ? (
            <p className="text-muted-foreground py-8 text-center text-sm text-balance">
              {snapshot.root_found ? "Nothing is tracked yet. " : "No PR Chain state on this machine. "}
              Run <code className="text-foreground font-mono text-xs whitespace-nowrap">pr-chain run &lt;pr number&gt;</code> or{" "}
              <code className="text-foreground font-mono text-xs whitespace-nowrap">pr-chain issue &lt;issue number&gt;</code> and it shows up here.
            </p>
          ) : (
            <>
              <ul className="divide-border divide-y" aria-label="Tracked items">
                {items.map((item) => {
                  const tone = statusTone(item);
                  return (
                    <li key={`${item.kind}-${item.number}`} className="grid grid-cols-[5.5rem_1fr] items-baseline sm:grid-cols-[5.5rem_1fr_auto] gap-x-3 gap-y-0.5 py-2 text-sm">
                      <a
                        href={itemUrl(item)}
                        target="_blank"
                        rel="noreferrer"
                        className="hover:text-primary flex items-center gap-1 font-mono text-xs max-lg:min-h-11"
                      >
                        {item.kind === "pr" ? "PR" : "Issue"} {item.number}
                      </a>
                      <span className={cn("min-w-0 break-words font-medium", TONE[tone])}>
                        {item.status ? statusLabel(item.status) : <span className="text-muted-foreground font-normal">No status yet</span>}
                        {item.claimed ? <span className="text-muted-foreground font-normal"> · Running</span> : null}
                      </span>
                      <span className="text-muted-foreground col-span-2 font-mono text-xs sm:col-span-1 sm:whitespace-nowrap">
                        {item.round ? `Round ${item.round}` : ""}
                        {item.round && item.updated ? " · " : ""}
                        {shortTime(item.updated)}
                      </span>
                      {item.why || item.last_verdict ? (
                        <span
                          className="text-muted-foreground col-span-2 min-w-0 break-words text-xs sm:col-span-3"
                        >
                          {[item.last_verdict ? statusLabel(item.last_verdict) : "", item.why].filter(Boolean).join(" · ")}
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
                        <span className="min-w-0 break-words">{describeEvent(event)}</span>
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
