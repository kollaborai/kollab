import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Monitor } from "lucide-react";
import type { EngineApi } from "@/api";
import { titleCase } from "@/components/panels/panel-model";
import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const POLL_MS = 2000;
const OUTPUT_LINES = 120;
/** Within this many px of the bottom still counts as following the tail. */
const FOLLOW_SLACK_PX = 24;

type Tone = "working" | "idle" | "waiting" | "dreaming" | "offline";
type StateChip = { label: string; tone: Tone };

/** Presence state strings from plugins/hub: AgentState, AgentLifecycle, PresenceState. */
const STATE_CHIPS: Record<string, StateChip> = {
  working: { label: "Working", tone: "working" },
  thinking: { label: "Working", tone: "working" },
  active: { label: "Working", tone: "working" },
  idle: { label: "Idle", tone: "idle" },
  ready: { label: "Idle", tone: "idle" },
  registered: { label: "Idle", tone: "idle" },
  booting: { label: "Starting", tone: "idle" },
  connecting: { label: "Starting", tone: "idle" },
  waiting: { label: "Waiting", tone: "waiting" },
  blocked: { label: "Blocked", tone: "waiting" },
  dreaming: { label: "Dreaming", tone: "dreaming" },
  disconnecting: { label: "Offline", tone: "offline" },
  dying: { label: "Offline", tone: "offline" },
  dead: { label: "Offline", tone: "offline" },
  offline: { label: "Offline", tone: "offline" },
};

const TONE_CLASS: Record<Tone, string> = {
  working:
    "border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-900 dark:bg-violet-950 dark:text-violet-200",
  waiting:
    "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200",
  dreaming:
    "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-200",
  idle: "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-200",
  offline: "border-dashed text-muted-foreground",
};

/** Label and tone for a presence state string; null when there is no state to show. */
export function describeAgentState(state?: string): StateChip | null {
  const key = state?.trim().toLowerCase();
  if (!key) return null;
  return STATE_CHIPS[key] ?? { label: titleCase(key), tone: "idle" };
}

const ANSI =
  /\u001b\[[0-9;?]*[ -/]*[@-~]|\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)|\u001b[()][0-9A-B]/g;
/** The solid-block box edges (a line of ▄ or ▀) the terminal draws around messages. */
const BLOCK_EDGE = /^(?=.*[▀-▟])[\s▀-▟]+$/;

/** A terminal render as plain text: no escape codes, no padding, no box-edge lines. */
export function cleanAgentOutput(raw: string): string {
  return raw
    .replace(ANSI, "")
    .replace(/\r\n/g, "\n")
    .replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, "")
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line) => !BLOCK_EDGE.test(line))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/^\n+|\n+$/g, "");
}

/** Pulses violet while the agent is working, muted otherwise. */
export function LiveIndicator({
  state,
  className,
}: {
  state?: string;
  className?: string;
}) {
  const chip = describeAgentState(state);
  return (
    <Monitor
      role={chip ? "img" : undefined}
      aria-label={chip?.label}
      aria-hidden={chip ? undefined : true}
      className={cn(
        "size-4 shrink-0",
        chip?.tone === "working"
          ? "text-violet-600 motion-safe:animate-pulse dark:text-violet-300"
          : "text-muted-foreground",
        className,
      )}
    />
  );
}

function StateBadge({ chip }: { chip: StateChip }) {
  return (
    <Badge variant="outline" className={cn("shrink-0", TONE_CLASS[chip.tone])}>
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full bg-current",
          chip.tone === "working" && "motion-safe:animate-pulse",
        )}
      />
      {chip.label}
    </Badge>
  );
}

/** `text` is null until the first poll lands; `error` is the last failed poll's message. */
type Feed = { text: string | null; error: string | null };
const EMPTY_FEED: Feed = { text: null, error: null };

const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

/** Polls one agent's recent output while `active`, pausing when the tab is hidden. */
function useAgentOutput(
  api: EngineApi,
  agentId: string | null,
  active: boolean,
): Feed {
  const [feed, setFeed] = useState<Feed>(EMPTY_FEED);

  useEffect(() => {
    if (!active || !agentId) return;
    setFeed(EMPTY_FEED);
    let timer: number | undefined;
    let controller: AbortController | undefined;

    const stop = () => {
      window.clearTimeout(timer);
      controller?.abort();
    };
    const poll = async () => {
      controller = new AbortController();
      const { signal } = controller;
      try {
        const res = await api.getHubAgentOutput(agentId, OUTPUT_LINES, signal);
        if (signal.aborted) return;
        const output = res.output;
        setFeed((prev) =>
          typeof output === "string"
            ? prev.text === output && !prev.error
              ? prev
              : { text: output, error: null }
            : { text: prev.text, error: res.error || "The agent sent no output." },
        );
      } catch (error) {
        if (signal.aborted) return;
        setFeed((prev) => ({ text: prev.text, error: errorText(error) }));
      }
      timer = window.setTimeout(poll, POLL_MS);
    };
    const sync = () => {
      stop();
      if (!document.hidden) void poll();
    };

    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      document.removeEventListener("visibilitychange", sync);
      stop();
    };
  }, [api, agentId, active]);

  return feed;
}

function ErrorNote({ message, stale }: { message: string; stale: boolean }) {
  return (
    <div
      role="alert"
      className="border-destructive/30 bg-destructive/10 text-destructive flex min-w-0 items-start gap-2 rounded-md border px-3 py-2 text-sm"
    >
      <AlertCircle className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">
        <p className="font-medium">
          {stale ? "Showing the Last Output" : "Could Not Load Output"}
        </p>
        <p className="break-words">{message}</p>
        <p className="opacity-80">Retrying every {POLL_MS / 1000} seconds.</p>
      </div>
    </div>
  );
}

export type AgentLivePanelProps = {
  /** Carries the engine URL and token; the panel only reads output through it. */
  api: EngineApi;
  agentId: string | null;
  identity?: string;
  state?: string;
  currentTask?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

/**
 * Right-side panel that tails one hub agent's screen without leaving the chat.
 * Non-modal: no backdrop, and clicks in the chat neither close nor block it.
 */
export function AgentLivePanel({
  api,
  agentId,
  identity,
  state,
  currentTask,
  open,
  onOpenChange,
}: AgentLivePanelProps) {
  const feed = useAgentOutput(api, agentId, open);
  const text = useMemo(
    () => (feed.text === null ? null : cleanAgentOutput(feed.text)),
    [feed.text],
  );
  const chip = describeAgentState(state);
  const task = currentTask?.trim();

  const panelRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const following = useRef(true);

  useEffect(() => {
    following.current = true;
  }, [agentId, open]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && following.current) el.scrollTop = el.scrollHeight;
  }, [text, feed.error]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    following.current =
      el.scrollHeight - el.scrollTop - el.clientHeight <= FOLLOW_SLACK_PX;
  };

  const body = !agentId ? (
    <p className="text-muted-foreground text-sm">
      Select an agent to follow its live work.
    </p>
  ) : text === null ? (
    feed.error ? (
      <ErrorNote message={feed.error} stale={false} />
    ) : (
      <div role="status" aria-label="Loading output" className="space-y-2">
        <Skeleton className="h-3 w-3/4" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-5/6" />
      </div>
    )
  ) : (
    <div className="space-y-3">
      {feed.error ? <ErrorNote message={feed.error} stale /> : null}
      {text ? (
        <pre className="font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">
          {text}
        </pre>
      ) : (
        <p className="text-muted-foreground text-sm">
          No output yet. Lines appear here as the agent works.
        </p>
      )}
    </div>
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange} modal={false}>
      <SheetContent
        ref={panelRef}
        side="right"
        className="w-full gap-0 p-0 sm:max-w-md"
        onInteractOutside={(event) => event.preventDefault()}
        // The composer cancels a run on Escape; only close when focus is in here.
        onEscapeKeyDown={(event) => {
          if (!panelRef.current?.contains(document.activeElement)) {
            event.preventDefault();
          }
        }}
      >
        <SheetHeader className="gap-2 border-b pr-12">
          <div className="flex min-w-0 items-center gap-2">
            <LiveIndicator state={state} />
            <SheetTitle className="truncate text-base">
              {identity ? titleCase(identity) : "Agent"}
            </SheetTitle>
            {chip ? <StateBadge chip={chip} /> : null}
          </div>
          <SheetDescription
            className="line-clamp-2 break-words"
            title={task || undefined}
          >
            {task
              ? `Task: ${task}`
              : currentTask === undefined
                ? "Live output from this agent."
                : "No active task."}
          </SheetDescription>
        </SheetHeader>
        <div
          ref={scrollRef}
          onScroll={onScroll}
          role="region"
          aria-label="Agent output"
          tabIndex={0}
          className="focus-visible:ring-ring/50 min-h-0 flex-1 overflow-y-auto px-4 py-3 outline-none focus-visible:ring-2 focus-visible:ring-inset"
        >
          {body}
        </div>
      </SheetContent>
    </Sheet>
  );
}
