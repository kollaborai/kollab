import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, RefreshCw, Search } from "lucide-react";
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
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(false);
  const refreshSequenceRef = useRef(0);

  const refreshHistory = useCallback(
    async (initial = false) => {
      const sequence = ++refreshSequenceRef.current;
      if (initial) setLoading(true);
      else setRefreshing(true);
      try {
        const result = await api.getHistory(sessionId);
        if (
          !mountedRef.current ||
          sequence !== refreshSequenceRef.current
        ) {
          return;
        }
        setRecords(projectTrajectory(result.history || []));
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

  const selectedRecord =
    records.find((record) => record.id === selectedId) || null;
  const isMobile = useIsMobile();

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      data-testid="trajectory-view"
    >
      <div className="flex shrink-0 flex-col gap-3 border-b px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <div>
              <h2 className="text-sm font-semibold">Trajectory</h2>
              <p className="text-muted-foreground text-xs">
                {visibleRecords.length} of {records.length} records
              </p>
            </div>
            {refreshing && (
              <Badge variant="secondary" className="font-normal">
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
                placeholder="Search trajectory"
                aria-label="Search trajectory"
                className="h-8 pl-8 text-xs"
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
            >
              <RefreshCw className={cn("size-4", refreshing && "animate-spin")} />
              <span className="hidden sm:inline">Refresh</span>
            </Button>
          </div>
        </div>
        {error && (
          <div className="text-destructive flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs">
            <AlertCircle className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0 break-words">{error}</span>
          </div>
        )}
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden md:flex-row">
        <TrajectoryTable
          records={visibleRecords}
          selectedId={selectedId}
          loading={loading}
          onSelect={setSelectedId}
        />
        <div className="hidden min-h-0 w-[min(26rem,38%)] md:flex">
          <TrajectoryInspector record={selectedRecord} className="w-full" />
        </div>
      </div>
      <Sheet
        open={isMobile && Boolean(selectedRecord)}
        onOpenChange={(open) => {
          if (!open) setSelectedId(null);
        }}
      >
        <SheetContent side="bottom" className="max-h-[75vh] p-0">
          <SheetHeader className="sr-only">
            <SheetTitle>Trajectory record details</SheetTitle>
          </SheetHeader>
          <TrajectoryInspector record={selectedRecord} className="min-h-0 border-0" />
        </SheetContent>
      </Sheet>
    </div>
  );
}
