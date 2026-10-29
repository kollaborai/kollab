import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, CheckCircle2, RefreshCw } from "lucide-react";
import type {
  EngineApi,
  PanelAction,
  PanelActionResult,
  PanelDescription,
  PanelField,
  PanelForm,
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
import { SESSIONLESS_PANELS } from "@/api";
import { confirmedPayload } from "./confirm";
import { serializeDraft, type Draft } from "./FieldRenderer";
import { FormPanel } from "./FormPanel";
import { NetworkPanel, type NetworkSection } from "./NetworkPanel";
import { PickerList } from "./PickerList";
import { PromptsPage } from "@/components/prompts/PromptsPage";
import { PromptsApi } from "@/prompts-api";
import { SessionSettings } from "./SessionSettings";
import { PhoneLinkPanel } from "./PhoneLinkPanel";
import { WizardSteps, wizardValues } from "./WizardSteps";
import {
  PANEL_NAME,
  SETTINGS_TABS,
  parseExpiry,
  pickerControlNeedsApply,
  settleControlSubmission,
  type PanelControlSubmission,
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
  voice: IDLE,
  prompts: IDLE,
});
const newView = (panel: PanelDescription): View => ({
  panel,
  draft: {},
  step: 0,
  errors: {},
  message: null,
});
/**
 * What a form action sends as `changes`. A settings form sends only the edits.
 * An action form (it carries `context`, like a loadout) sends every editable
 * field, so accepting the prefilled defaults still reaches the daemon.
 */
function formChanges(panel: PanelForm, draft: Draft): Record<string, unknown> {
  const edits = serializeDraft(draft);
  if (!panel.context) return edits;
  const values: Record<string, unknown> = {};
  for (const section of panel.sections) {
    for (const field of section.fields) {
      if (field.type === "label" || !field.editable || field.secret) continue;
      if (field.value !== null && field.value !== undefined) {
        values[field.path] = field.value;
      }
    }
  }
  return { ...values, ...edits };
}
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
      className="mx-4 mt-3 flex min-w-0 flex-col gap-2 rounded-lg bg-amber-500/10 p-3 sm:mx-6"
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
  displayName,
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
  /** The session's agent by its persona name, when it has one. */
  displayName?: string;
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
  // the session-less halt panels (SESSIONLESS_PANELS) ask about this chat's folder
  if (session.workspace) api.haltFolder = session.workspace;
  // The Prompts tab renders fully client-side against the prompt-section API.
  const promptsApi = useMemo(() => new PromptsApi(api), [api]);
  const [tabs, setTabs] = useState<TabMap>(freshTabs);
  const [busy, setBusy] = useState(false);
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const [poll, setPoll] = useState<Poll | null>(null);
  const [networkSection, setNetworkSection] = useState<"overview" | "access">("overview");
  // Bumped on close: answers that arrive for an older window are dropped.
  const epoch = useRef(0);
  const inflight = useRef<Partial<Record<PanelTab, Promise<void>>>>({});
  const openingKnocks = useRef<Promise<void> | null>(null);
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
        // No chat open (the sidebar's halted line): Network opens on Halt and Resume.
        .getPanel(sessionId, sessionId ? PANEL_NAME[target] : "connect-halt")
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
    if (
      open &&
      tab !== "session" &&
      tab !== "prompts" &&
      tab !== "phonelink" &&
      tabs[tab].status === "idle"
    ) {
      void loadRoot(tab);
    }
  }, [open, tab, tabs, loadRoot]);

  // Closing drops everything: drafts, join codes, polls, in-flight answers.
  useEffect(() => {
    if (open) return;
    epoch.current += 1;
    inflight.current = {};
    openingKnocks.current = null;
    setTabs(freshTabs());
    setReveal(null);
    setPoll(null);
    setBusy(false);
    setNetworkSection("overview");
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
      submitted?: PanelControlSubmission,
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
        const settled = submitted ? settleControlSubmission(top, submitted, result) : null;
        const errors = settled?.errors ?? result.errors ?? {};
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
          draft: settled?.draft ?? (result.ok && reset ? {} : top.draft),
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
      opts: { reset?: boolean; poll?: boolean; submitted?: PanelControlSubmission } = {},
    ) => {
      const mine = epoch.current;
      if (!opts.poll) setBusy(true);
      try {
        const result = await api.panelAction(sessionId, name, action, payload);
        if (mine !== epoch.current) return;
        applyResult(target, name, result, Boolean(opts.reset), opts.submitted);
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
          ...(opts.submitted
            ? settleControlSubmission(view, opts.submitted, { ok: false, message: errorText(error) })
            : { errors: {} }),
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
    const pushed = intent.action === "halt" ? "connect-halt" : "connect-knocks";
    if (pushed === "connect-halt" && !sessionId) return; // already the root
    if (intent.action === "new_code") {
      setNetworkSection("access");
      setReveal(null);
      patchTab("network", (state) => ({
        ...state,
        views: state.views[state.views.length - 1]?.panel.panel === "connect-knocks"
          ? state.views.slice(0, -1)
          : state.views,
      }));
      void runAction("network", "connect", "new_code");
    } else {
      setReveal(null);
      const mine = epoch.current;
      void api.getPanel(sessionId, pushed).then(
        (panel) => {
          if (mine !== epoch.current) return;
          patchTab("network", (state) => ({
            ...state,
            views: state.views[state.views.length - 1]?.panel.panel === pushed
              ? [...state.views.slice(0, -1), newView(panel)]
              : [...state.views, newView(panel)],
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

  const state = tab === "session" || tab === "prompts" || tab === "phonelink" ? null : tabs[tab];
  const top = state?.views[state.views.length - 1];
  const activeTab =
    SETTINGS_TABS.find((item) => item.id === tab) ?? SETTINGS_TABS[0];
  const message = top?.message ?? null;

  const refresh = async () => {
    if (tab === "session" || tab === "prompts" || tab === "phonelink") return;
    if (tab === "network" && top?.panel.panel === "connect-knocks") {
      const mine = epoch.current;
      setBusy(true);
      try {
        const panel = await api.getPanel(sessionId, top.panel.panel);
        if (mine === epoch.current) {
          patchTop("network", (view) => ({ ...view, panel, errors: {}, message: null }));
        }
      } catch (error) {
        if (mine === epoch.current) {
          patchTop("network", (view) => ({ ...view, message: { ok: false, text: errorText(error) } }));
        }
      } finally {
        if (mine === epoch.current) setBusy(false);
      }
      return;
    }
    if (
      dirtyCount > 0 &&
      !window.confirm("Reloading discards your unsaved changes. Continue?")
    ) {
      return;
    }
    patchTab(tab, () => IDLE);
  };
  const back = () => {
    if (tab === "session" || tab === "prompts" || tab === "phonelink") return;
    setReveal(null);
    patchTab(tab, (current) => ({
      ...current,
      views: current.views.slice(0, -1),
    }));
  };

  const changeNetworkSection = (next: NetworkSection) => {
    // Pointer activation and focus can both arrive before busy re-renders.
    if (busy || openingKnocks.current) return;
    setReveal(null);
    if (next === "knocks") {
      if (top?.panel.panel !== "connect-knocks") {
        const request = runAction("network", "connect", "knocks");
        openingKnocks.current = request;
        void request.finally(() => {
          if (openingKnocks.current === request) openingKnocks.current = null;
        });
      }
      return;
    }
    setNetworkSection(next);
    patchTab("network", (current) => current.views[current.views.length - 1]?.panel.panel === "connect-knocks"
      ? { ...current, views: current.views.slice(0, -1) }
      : current);
  };

  const panelBody = () => {
    if (!state || tab === "session" || tab === "prompts" || tab === "phonelink") return null;
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
    const root = tab === "network"
      ? state.views.find((view) => view.panel.panel === "connect" && view.panel.kind === "picker")?.panel
      : undefined;
    const networkRoot = root?.kind === "picker" ? root : undefined;
    const networkPicker = Boolean(networkRoot && panel.kind === "picker" && (name === "connect" || name === "connect-knocks"));
    const networkKnocksTab = networkPicker && name === "connect-knocks" && state.views.length === 2;
    // Halt, resume and revoke actions reach this point only after the person's second press (confirm.ts).
    const sure = (action: PanelAction, payload: Record<string, unknown> = {}) =>
      confirmedPayload(SESSIONLESS_PANELS.has(name), action.confirm, payload);
    const onRowAction = (action: PanelAction, row: PanelRow) =>
      void runAction(
        tab,
        name,
        action.id,
        sure(action, {
          id: row.id,
          ...(action.payload_key ? { [action.payload_key]: row.id } : {}),
        }),
      );
    const onToolbarAction = (action: PanelAction) => void runAction(tab, name, action.id, sure(action));
    const onControlChange = (field: PanelField, value: unknown, resetDraft = false) =>
      void runAction(tab, name, field.action || field.path, { [field.path]: value }, {
        reset: resetDraft,
        ...(pickerControlNeedsApply(name, field) ? { submitted: { path: field.path, value } } : {}),
      });
    return (
      <>
        <div className="flex shrink-0 items-center gap-2 px-4 pt-3 sm:px-6">
          {state.views.length > 1 && !networkKnocksTab ? (
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
            {networkPicker && networkRoot ? networkRoot.title : panel.title}
          </h3>
          {state.views.length === 1 || networkKnocksTab ? (
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
              "mx-4 mt-3 flex min-w-0 items-start gap-2 rounded-md px-3 py-2 text-sm sm:mx-6",
              message.ok
                ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                : "bg-destructive/10 text-destructive",
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
                sure(action, {
                  changes: formChanges(panel, top.draft),
                  ...(target ? { target } : {}),
                  ...(panel.context ? { context: panel.context } : {}),
                }),
                { reset: true },
              )
            }
          />
        ) : panel.kind === "picker" ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 sm:px-6">
            {networkPicker && networkRoot ? (
              <NetworkPanel
                panel={panel}
                rootPanel={networkRoot}
                section={name === "connect-knocks" ? "knocks" : networkSection}
                draft={top.draft}
                errors={top.errors}
                busy={busy}
                onSectionChange={changeNetworkSection}
                onDraftChange={(draft) => patchTop(tab, (view) => ({ ...view, draft }))}
                onRowAction={onRowAction}
                onToolbarAction={onToolbarAction}
                onControlChange={onControlChange}
              />
            ) : (
              <PickerList
                panel={panel}
                draft={top.draft}
                onDraftChange={(draft) => patchTop(tab, (view) => ({ ...view, draft }))}
                busy={busy}
                errors={top.errors}
                onRowAction={onRowAction}
                onToolbarAction={onToolbarAction}
                onControlChange={onControlChange}
              />
            )}
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
          className="mx-4 w-auto shrink-0 justify-start overflow-x-auto [scrollbar-width:none] sm:mx-6 [&::-webkit-scrollbar]:hidden"
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
        <div className="mt-3 flex min-h-0 flex-1 flex-col">
          <div
            role="tabpanel"
            aria-label="Session"
            className={cn(
              "mx-auto min-h-0 w-full max-w-3xl flex-1 flex-col",
              tab === "session" ? "flex" : "hidden",
            )}
          >
            <SessionSettings
              api={api}
              session={session}
              displayName={displayName}
              profiles={profiles}
              onSessionUpdated={onSessionUpdated}
            />
          </div>
          <div
            role="tabpanel"
            aria-label="Prompts"
            className={cn(
              "min-h-0 flex-1 flex-col",
              tab === "prompts" ? "flex" : "hidden",
            )}
          >
            <PromptsPage api={promptsApi} />
          </div>
          {tab === "phonelink" && open ? (
            <div role="tabpanel" aria-label="PhoneLink" className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col">
              <PhoneLinkPanel api={api} onOpenNetwork={() => changeTab("network")} />
            </div>
          ) : null}
          {tab !== "session" && tab !== "prompts" && tab !== "phonelink" ? (
            <div
              role="tabpanel"
              aria-label={activeTab.label}
              className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col"
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
        <DialogContent className="flex h-[min(52rem,92dvh)] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-[min(72rem,94vw)]">
          {body}
        </DialogContent>
      )}
    </Dialog>
  );
}
