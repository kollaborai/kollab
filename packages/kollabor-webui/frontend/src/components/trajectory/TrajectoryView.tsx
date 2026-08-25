import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertCircle, RefreshCw, Search, Wrench } from "lucide-react";
import type { EngineApi, SessionEvent } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import { TrajectoryInspector } from "./TrajectoryInspector";
import { projectTrajectory, type TrajectoryRecord } from "./trajectory-records";
import { TrajectoryTable } from "./TrajectoryTable";

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function waitForRetry(
  signal: AbortSignal,
  delayMs: number,
  timerRef: { current: number | null },
): Promise<void> {
  return new Promise((resolve) => {
    const finish = () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      signal.removeEventListener("abort", finish);
      resolve();
    };
    timerRef.current = window.setTimeout(finish, delayMs);
    signal.addEventListener("abort", finish, { once: true });
  });
}

export function TrajectoryView({
  api,
  sessionId,
}: {
  api: EngineApi;
  sessionId: string;
}) {
  const [records, setRecords] = useState<TrajectoryRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collapseTurns, setCollapseTurns] = useState(false);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [canLoadEarlier, setCanLoadEarlier] = useState(false);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(false);
  const refreshSequenceRef = useRef(0);
  const historyLimitRef = useRef(200);

  const refreshHistory = useCallback(
    async (initial = false) => {
      const sequence = ++refreshSequenceRef.current;
      if (initial) setLoading(true);
      else setRefreshing(true);
      try {
        const result = await api.getHistory(sessionId, historyLimitRef.current);
        if (
          !mountedRef.current ||
          sequence !== refreshSequenceRef.current
        ) {
          return;
        }
        const history = result.history || [];
        setRecords(projectTrajectory(history));
        setCanLoadEarlier(history.length >= historyLimitRef.current);
        setError(null);
      } catch (reason) {
        if (
          mountedRef.current &&
          sequence === refreshSequenceRef.current
        ) {
          setError(errorMessage(reason));
        }
      } finally {
        if (
          mountedRef.current &&
          sequence === refreshSequenceRef.current
        ) {
          setLoading(false);
          setRefreshing(false);
        }
      }
  },
  [api, sessionId],
  );

  const loadEarlier = useCallback(async () => {
    if (loadingEarlier || !canLoadEarlier) return;
    const nextLimit = historyLimitRef.current + 200;
    historyLimitRef.current = nextLimit;
    setLoadingEarlier(true);
    try {
      await refreshHistory();
    } finally {
      if (mountedRef.current) setLoadingEarlier(false);
    }
  }, [canLoadEarlier, loadingEarlier, refreshHistory]);

  useEffect(() => {
    mountedRef.current = true;
    const controller = new AbortController();
    const retryTimerRef = { current: null as number | null };

    void refreshHistory(true);

    const followEvents = async () => {
      while (mountedRef.current && !controller.signal.aborted) {
        try {
          await api.streamEvents(
            sessionId,
            controller.signal,
            (event: SessionEvent) => {
              if (event.type === "turn_complete" || event.type === "error") {
                // History is a settled-view surface: token/tool events stay
                // on the transport, while completed/error boundaries trigger
                // one fresh projection.
                void refreshHistory();
              }
            },
          );
        } catch (reason) {
          if (controller.signal.aborted || !mountedRef.current) break;
          setError(errorMessage(reason));
          await waitForRetry(controller.signal, 1000, retryTimerRef);
          continue;
        }
        if (!mountedRef.current || controller.signal.aborted) break;
        // GET /events intentionally ends at turn_complete. Reconnect so a
        // trajectory tab left open can observe the next turn as well.
        await waitForRetry(controller.signal, 50, retryTimerRef);
      }
    };

    void followEvents();
    return () => {
      mountedRef.current = false;
      controller.abort();
      if (retryTimerRef.current !== null) {
        window.clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [api, refreshHistory, sessionId]);

  useEffect(() => {
    if (selectedId && !records.some((record) => record.id === selectedId)) {
      setSelectedId(null);
    }
  }, [records, selectedId]);

  const visibleRecords = useMemo(() => {
    const query = search.trim().toLowerCase();
    return records.filter((record) => {
      if (
        collapseTurns &&
        (record.kind === "tool" || record.kind === "tool-batch")
      ) {
        return false;
      }
      if (!query) return true;
      return `${record.title} ${record.summary} ${record.kind}`
        .toLowerCase()
        .includes(query);
    });
  }, [collapseTurns, records, search]);

  const toolCount = useMemo(
    () =>
      records.filter(
        (record) => record.kind === "tool" || record.kind === "tool-batch",
      ).length,
    [records],
  );
  const errorCount = useMemo(
    () => records.filter((record) => record.isError).length,
    [records],
  );

  const selectedRecord =
    records.find((record) => record.id === selectedId) || null;
  // The two-pane inspector needs more room than the global mobile breakpoint
  // allows. Keep the ledger readable on compact laptop/tablet widths and use
  // the same bottom sheet interaction there.
  const isMobile = useIsMobile(1024);

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      data-testid="trajectory-view"
    >
      <div className="flex shrink-0 flex-col gap-3 border-b bg-muted/[0.06] px-4 py-3.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <div className="bg-primary/10 text-primary flex size-8 shrink-0 items-center justify-center rounded-lg ring-1 ring-primary/10">
              <Activity className="size-4" />
            </div>
            <div className="min-w-0">
              <p className="text-muted-foreground text-[10px] font-semibold tracking-[0.16em] uppercase">
                Session trace
              </p>
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <h2 className="text-sm font-semibold">Trajectory</h2>
                <p className="text-muted-foreground text-xs">
                  {visibleRecords.length} of {records.length} records
                  <span className="mx-1.5">·</span>
                  {toolCount} {toolCount === 1 ? "tool" : "tools"}
                  {errorCount > 0 && (
                    <>
                      <span className="mx-1.5">·</span>
                      <span className="text-destructive">
                        {errorCount} {errorCount === 1 ? "error" : "errors"}
                      </span>
                    </>
                  )}
                </p>
              </div>
            </div>
            {refreshing && (
              <Badge variant="secondary" className="gap-1.5 font-normal">
                <RefreshCw className="size-3 animate-spin" />
                refreshing
              </Badge>
            )}
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <div className="relative w-48 sm:w-64">
              <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search records"
                aria-label="Search trajectory"
                className="h-8 rounded-lg bg-background/70 pl-8 text-xs shadow-xs"
              />
            </div>
            <label className="text-muted-foreground flex items-center gap-2 text-xs">
              <Switch
                checked={collapseTurns}
                onCheckedChange={setCollapseTurns}
                aria-label="Collapse tool rows"
                size="sm"
              />
              <span className="hidden sm:inline">Collapse tools</span>
            </label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void refreshHistory()}
              disabled={refreshing}
              aria-label="Refresh trajectory"
              className="rounded-lg bg-background/70 shadow-xs"
            >
              <RefreshCw className={cn("size-4", refreshing && "animate-spin")} />
              <span className="hidden sm:inline">Refresh</span>
            </Button>
          </div>
        </div>
        {error && (
          <div className="text-destructive flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs shadow-xs">
            <AlertCircle className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0 break-words">{error}</span>
          </div>
        )}
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-3 md:p-4">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border bg-card/40 shadow-sm lg:flex-row">
          <TrajectoryTable
            records={visibleRecords}
            selectedId={selectedId}
            loading={loading}
            canLoadEarlier={canLoadEarlier}
            loadingEarlier={loadingEarlier}
            onLoadEarlier={() => void loadEarlier()}
            onSelect={setSelectedId}
          />
          <div className="hidden min-h-0 min-w-0 w-[min(30rem,42%)] lg:flex">
            <TrajectoryInspector record={selectedRecord} className="w-full" />
          </div>
        </div>
      </div>
      <Sheet
        open={isMobile && Boolean(selectedRecord)}
        onOpenChange={(open) => {
          if (!open) setSelectedId(null);
        }}
      >
        <SheetContent side="bottom" className="max-h-[75vh] rounded-t-2xl p-0">
          <SheetHeader className="sr-only">
            <SheetTitle>Trajectory record details</SheetTitle>
          </SheetHeader>
          <TrajectoryInspector record={selectedRecord} className="min-h-0 border-0" />
        </SheetContent>
      </Sheet>
    </div>
  );
}
