import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, CheckCircle2, RefreshCw } from "lucide-react";
import type {
  EngineApi,
  PanelAction,
  PanelActionResult,
  PanelDescription,
  PanelField,
  PanelReveal,
  PanelRow,
  Profile,
  Session,
} from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { SheetContent } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import { serializeDraft, type Draft } from "./FieldRenderer";
import { FormPanel } from "./FormPanel";
import { PickerList } from "./PickerList";
import { SessionSettings } from "./SessionSettings";
import { WizardSteps, wizardValues } from "./WizardSteps";
import {
  PANEL_NAME,
  SETTINGS_TABS,
  parseExpiry,
  type PanelIntent,
  type PanelTab,
  type SettingsTab,
} from "./panel-model";

type Message = { ok: boolean; text: string };
/** One screen on a tab's stack: the panel plus what the user has typed into it. */
type View = {
  panel: PanelDescription;
  draft: Draft;
  step: number;
  errors: Record<string, string>;
  message: Message | null;
};
type TabState = {
  status: "idle" | "loading" | "ready" | "error";
  views: View[];
  error: string | null;
};
type TabMap = Record<PanelTab, TabState>;
type Reveal = { tab: PanelTab; data: PanelReveal; expiresAt: number | null };
type Poll = {
  tab: PanelTab;
  name: string;
  action: string;
  payload: Record<string, unknown>;
  everyMs: number;
};

const IDLE: TabState = { status: "idle", views: [], error: null };
const freshTabs = (): TabMap => ({
  configuration: IDLE,
  loadouts: IDLE,
  model: IDLE,
  setup: IDLE,
  network: IDLE,
});
const newView = (panel: PanelDescription): View => ({
  panel,
  draft: {},
  step: 0,
  errors: {},
  message: null,
});
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

/** A one-time secret with a countdown. Deliberately no copy button. */
function RevealCard({
  reveal,
  onDismiss,
}: {
  reveal: Reveal;
  onDismiss: () => void;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (reveal.expiresAt === null) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [reveal.expiresAt]);
  const left =
    reveal.expiresAt === null
      ? null
      : Math.max(0, Math.ceil((reveal.expiresAt - now) / 1000));
  const clock =
    left === null
      ? null
      : `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  return (
    <section
      aria-label={reveal.data.label}
      className="mx-4 mt-3 flex min-w-0 flex-col gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 sm:mx-6"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium">{reveal.data.label}</span>
        {clock ? (
          <span role="timer" className="text-xs tabular-nums">
            Expires in {clock}
          </span>
        ) : null}
      </div>
      <code className="block min-w-0 font-mono text-lg font-semibold tracking-wider break-all">
        {reveal.data.value}
      </code>
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground text-xs break-words">
          {reveal.data.status || "Shown once. Closing this window removes it."}
        </span>
        <Button type="button" size="xs" variant="outline" onClick={onDismiss}>
          Done
        </Button>
      </div>
    </section>
  );
}

/**
 * Settings: the Session form plus one tab per daemon panel. All open state is
 * owned by the caller, so every entry point (both Settings buttons, the
 * composer) calls the same setter. The daemon owns the screens; this renders
 * whatever `describe()` returns and re-renders from each action's response.
 */
export function PanelHost({
  api,
  session,
  profiles,
  open,
  onOpenChange,
  tab,
  onTabChange,
  intent,
  onChanged,
  onSessionUpdated,
}: {
  api: EngineApi;
  session: Session;
  profiles: Profile[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tab: SettingsTab;
  onTabChange: (tab: SettingsTab) => void;
  intent: { action: PanelIntent; nonce: number } | null;
  /** An action changed something other views show (profile, model, session). */
  onChanged: () => void;
  onSessionUpdated: (session: Session) => void;
}) {
  const isMobile = useIsMobile();
  const sessionId = session.session_id;
  const [tabs, setTabs] = useState<TabMap>(freshTabs);
  const [busy, setBusy] = useState(false);
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const [poll, setPoll] = useState<Poll | null>(null);
  // Bumped on close: answers that arrive for an older window are dropped.
  const epoch = useRef(0);
  const inflight = useRef<Partial<Record<PanelTab, Promise<void>>>>({});
  const consumed = useRef(0);
  const onChangedRef = useRef(onChanged);
  useEffect(() => {
    onChangedRef.current = onChanged;
  });

  const patchTab = useCallback(
    (target: PanelTab, change: (state: TabState) => TabState) =>
      setTabs((current) => ({ ...current, [target]: change(current[target]) })),
    [],
  );
  const patchTop = useCallback(
    (target: PanelTab, change: (view: View) => View) =>
      patchTab(target, (state) =>
        state.views.length
          ? {
              ...state,
              views: [
                ...state.views.slice(0, -1),
                change(state.views[state.views.length - 1]),
              ],
            }
          : state,
      ),
    [patchTab],
  );

  const loadRoot = useCallback(
    (target: PanelTab): Promise<void> => {
      const running = inflight.current[target];
      if (running) return running;
      const mine = epoch.current;
      patchTab(target, (state) => ({ ...state, status: "loading", error: null }));
      const job: Promise<void> = api
        .getPanel(sessionId, PANEL_NAME[target])
        .then(
          (panel) => {
            if (mine !== epoch.current) return;
            patchTab(target, () => ({
              status: "ready",
              views: [newView(panel)],
              error: null,
            }));
          },
          (error) => {
            if (mine !== epoch.current) return;
            patchTab(target, () => ({
              status: "error",
              views: [],
              error: errorText(error),
            }));
          },
        )
        .finally(() => {
          if (inflight.current[target] === job) delete inflight.current[target];
        });
      inflight.current[target] = job;
      return job;
    },
    [api, sessionId, patchTab],
  );

  // Fetch a tab the first time it is shown.
  useEffect(() => {
    if (open && tab !== "session" && tabs[tab].status === "idle") {
      void loadRoot(tab);
    }
  }, [open, tab, tabs, loadRoot]);

  // Closing drops everything: drafts, join codes, polls, in-flight answers.
  useEffect(() => {
    if (open) return;
    epoch.current += 1;
    inflight.current = {};
    setTabs(freshTabs());
    setReveal(null);
    setPoll(null);
    setBusy(false);
  }, [open]);

  // A join code disappears when it expires.
  useEffect(() => {
    if (!reveal || reveal.expiresAt === null) return;
    const timer = window.setTimeout(
      () => setReveal(null),
      Math.max(0, reveal.expiresAt - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [reveal]);

  const applyResult = useCallback(
    (
      target: PanelTab,
      name: string,
      result: PanelActionResult,
      reset: boolean,
    ) => {
      const message: Message | null = result.message
        ? { ok: result.ok, text: result.message }
        : result.ok
          ? null
          : { ok: false, text: "That did not work." };
      patchTab(target, (state) => {
        if (!state.views.length) return state;
        const views = state.views.slice();
        const top = views[views.length - 1];
        const errors = result.errors ?? {};
        let step = top.step;
        if (!result.ok && top.panel.kind === "wizard") {
          // Show the first step that has a rejected field.
          const bad = top.panel.steps.findIndex((s) =>
            s.fields.some((f) => f.path in errors),
          );
          if (bad >= 0) step = bad;
        }
        views[views.length - 1] = {
          ...top,
          panel: result.ok && result.panel ? result.panel : top.panel,
          draft: result.ok && reset ? {} : top.draft,
          step: result.ok && reset ? 0 : step,
          errors,
          message,
        };
        if (result.open) views.push(newView(result.open));
        return { ...state, views };
      });
      if (result.reveal) {
        setReveal({
          tab: target,
          data: result.reveal,
          expiresAt: parseExpiry(result.reveal.expires_at),
        });
      }
      const next = result.poll ?? null;
      setPoll(
        next
          ? {
              tab: target,
              name,
              action: next.action,
              payload: next.payload ?? {},
              everyMs: Math.max(500, (next.every_s ?? 2) * 1000),
            }
          : null,
      );
      if (result.ok && !next) onChangedRef.current();
    },
    [patchTab],
  );

  const runAction = useCallback(
    async (
      target: PanelTab,
      name: string,
      action: string,
      payload: Record<string, unknown> = {},
      opts: { reset?: boolean; poll?: boolean } = {},
    ) => {
      const mine = epoch.current;
      if (!opts.poll) setBusy(true);
      try {
        const result = await api.panelAction(sessionId, name, action, payload);
        if (mine !== epoch.current) return;
        applyResult(target, name, result, Boolean(opts.reset));
        if (result.ok && !result.panel && !result.open && !result.poll) {
          // No fresh screen came back; ask for one so nothing shows stale.
          const fresh = await api.getPanel(sessionId, name);
          if (mine === epoch.current) {
            patchTop(target, (view) => ({ ...view, panel: fresh }));
          }
        }
      } catch (error) {
        if (mine !== epoch.current) return;
        patchTop(target, (view) => ({
          ...view,
          errors: {},
          message: { ok: false, text: errorText(error) },
        }));
        setPoll(null);
      } finally {
        if (!opts.poll && mine === epoch.current) setBusy(false);
      }
    },
    [api, sessionId, applyResult, patchTop],
  );

  // Re-send a poll action until a response stops asking for another round.
  useEffect(() => {
    if (!open || !poll) return;
    const timer = window.setTimeout(
      () =>
        void runAction(poll.tab, poll.name, poll.action, poll.payload, {
          poll: true,
        }),
      poll.everyMs,
    );
    return () => window.clearTimeout(timer);
  }, [open, poll, runAction]);

  // `/connect code` and `/connect knocks`: run once the Network tab is ready.
  const networkReady = tabs.network.status === "ready";
  useEffect(() => {
    if (!open || !intent || consumed.current === intent.nonce) return;
    if (tab !== "network" || !networkReady) return;
    consumed.current = intent.nonce;
    if (intent.action === "new_code") {
      void runAction("network", "connect", "new_code");
    } else {
      const mine = epoch.current;
      void api.getPanel(sessionId, "connect-knocks").then(
        (panel) => {
          if (mine !== epoch.current) return;
          patchTab("network", (state) => ({
            ...state,
            views: [...state.views, newView(panel)],
          }));
        },
        (error) => {
          if (mine !== epoch.current) return;
          patchTop("network", (view) => ({
            ...view,
            message: { ok: false, text: errorText(error) },
          }));
        },
      );
    }
  }, [open, intent, tab, networkReady, api, sessionId, runAction, patchTab, patchTop]);

  const dirtyCount = Object.values(tabs).reduce(
    (total, state) =>
      total +
      state.views.reduce((n, view) => n + Object.keys(view.draft).length, 0),
    0,
  );
  const handleOpenChange = (next: boolean) => {
    if (
      !next &&
      dirtyCount > 0 &&
      !window.confirm(
        `Discard ${dirtyCount} unsaved change${dirtyCount === 1 ? "" : "s"}?`,
      )
    ) {
      return;
    }
    onOpenChange(next);
  };
  const changeTab = (next: string) => {
    setReveal(null);
    onTabChange(next as SettingsTab);
  };

  const state = tab === "session" ? null : tabs[tab];
  const top = state?.views[state.views.length - 1];
  const activeTab =
    SETTINGS_TABS.find((item) => item.id === tab) ?? SETTINGS_TABS[0];
  const message = top?.message ?? null;

  const refresh = () => {
    if (tab === "session") return;
    if (
      dirtyCount > 0 &&
      !window.confirm("Reloading discards your unsaved changes. Continue?")
    ) {
      return;
    }
    patchTab(tab, () => IDLE);
  };
  const back = () => {
    if (tab === "session") return;
    setReveal(null);
    patchTab(tab, (current) => ({
      ...current,
      views: current.views.slice(0, -1),
    }));
  };

  const panelBody = () => {
    if (!state || tab === "session") return null;
    if (state.status === "error") {
      return (
        <div className="flex flex-col items-start gap-3 px-4 py-4 sm:px-6">
          <p role="alert" className="text-destructive text-sm break-words">
            {state.error}
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => patchTab(tab, () => IDLE)}
          >
            Try Again
          </Button>
        </div>
      );
    }
    if (!top) {
      return (
        <div className="flex flex-col gap-3 px-4 py-4 sm:px-6" aria-busy="true">
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-2/3" />
        </div>
      );
    }
    const name = top.panel.panel;
    const panel = top.panel;
    return (
      <>
        <div className="flex shrink-0 items-center gap-2 px-4 pt-3 sm:px-6">
          {state.views.length > 1 ? (
            <Button
              type="button"
              size="xs"
              variant="ghost"
              disabled={busy}
              onClick={back}
            >
              <ArrowLeft />
              Back
            </Button>
          ) : null}
          <h3 className="min-w-0 flex-1 truncate text-sm font-semibold">
            {panel.title}
          </h3>
          {state.views.length === 1 ? (
            <Button
              type="button"
              size="xs"
              variant="ghost"
              disabled={busy}
              onClick={refresh}
            >
              <RefreshCw />
              Refresh
            </Button>
          ) : null}
        </div>
        {message ? (
          <div
            role="status"
            className={cn(
              "mx-4 mt-3 flex min-w-0 items-start gap-2 rounded-md border px-3 py-2 text-sm sm:mx-6",
              message.ok
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                : "border-destructive/30 bg-destructive/10 text-destructive",
            )}
          >
            {message.ok ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
            ) : (
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
            )}
            <span className="min-w-0 break-words">{message.text}</span>
          </div>
        ) : null}
        {reveal && reveal.tab === tab ? (
          <RevealCard reveal={reveal} onDismiss={() => setReveal(null)} />
        ) : null}
        {panel.kind === "form" ? (
          <FormPanel
            panel={panel}
            draft={top.draft}
            errors={top.errors}
            busy={busy}
            onDraftChange={(draft) => patchTop(tab, (view) => ({ ...view, draft }))}
            onAction={(action, target) =>
              void runAction(
                tab,
                name,
                action.id,
                {
                  changes: serializeDraft(top.draft),
                  ...(target ? { target } : {}),
                },
                { reset: true },
              )
            }
          />
        ) : panel.kind === "picker" ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 sm:px-6">
            <PickerList
              panel={panel}
              busy={busy}
              onRowAction={(action: PanelAction, row: PanelRow) =>
                void runAction(tab, name, action.id, {
                  id: row.id,
                  ...(action.payload_key ? { [action.payload_key]: row.id } : {}),
                })
              }
              onToolbarAction={(action) => void runAction(tab, name, action.id)}
              onControlChange={(field: PanelField, value) =>
                void runAction(tab, name, field.action || field.path, {
                  [field.path]: value,
                })
              }
            />
          </div>
        ) : panel.kind === "wizard" ? (
          <WizardSteps
            panel={panel}
            draft={top.draft}
            step={top.step}
            errors={top.errors}
            busy={busy}
            onDraftChange={(draft) => patchTop(tab, (view) => ({ ...view, draft }))}
            onStepChange={(step) => patchTop(tab, (view) => ({ ...view, step }))}
            onAction={(action, step) =>
              void runAction(
                tab,
                name,
                action.id,
                { values: wizardValues(panel, top.draft), step: step.id },
                { reset: action.id === "finish" },
              )
            }
          />
        ) : (
          <p className="text-muted-foreground px-4 py-4 text-sm sm:px-6">
            This screen type is not supported by this version of the web UI.
          </p>
        )}
      </>
    );
  };

  const body = (
    <>
      <div className="shrink-0 px-4 pt-4 pr-12 sm:px-6 sm:pt-5">
        <DialogTitle className="text-lg leading-none font-semibold">
          Settings
        </DialogTitle>
        <DialogDescription className="mt-1.5 break-words">
          {top?.panel.scope_note || activeTab.scope}
        </DialogDescription>
      </div>
      <Tabs
        value={tab}
        onValueChange={changeTab}
        className="mt-3 min-h-0 flex-1 gap-0"
      >
        <TabsList
          aria-label="Settings sections"
          className="mx-4 w-auto shrink-0 justify-start overflow-x-auto sm:mx-6"
        >
          {SETTINGS_TABS.map((item) => (
            <TabsTrigger
              key={item.id}
              value={item.id}
              className="flex-none px-2.5 sm:px-3"
            >
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <div className="mt-3 flex min-h-0 flex-1 flex-col border-t">
          <div
            role="tabpanel"
            aria-label="Session"
            className={cn(
              "min-h-0 flex-1 flex-col",
              tab === "session" ? "flex" : "hidden",
            )}
          >
            <SessionSettings
              api={api}
              session={session}
              profiles={profiles}
              onSessionUpdated={onSessionUpdated}
            />
          </div>
          {tab !== "session" ? (
            <div
              role="tabpanel"
              aria-label={activeTab.label}
              className="flex min-h-0 flex-1 flex-col"
            >
              {panelBody()}
            </div>
          ) : null}
        </div>
      </Tabs>
    </>
  );

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      {isMobile ? (
        <SheetContent
          side="right"
          className="h-dvh w-full max-w-full gap-0 overflow-hidden p-0 sm:max-w-full"
        >
          {body}
        </SheetContent>
      ) : (
        <DialogContent className="flex h-[min(46rem,90dvh)] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
          {body}
        </DialogContent>
      )}
    </Dialog>
  );
}
