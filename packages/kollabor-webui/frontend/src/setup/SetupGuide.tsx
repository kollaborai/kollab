import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, ChevronRight, X } from "lucide-react";
import type { EngineApi } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import {
  SETUP_STEPS,
  STORAGE_KEY,
  emptyFacts,
  loadGuideState,
  nextStep,
  resolveStep,
  saveGuideState,
  stepIndex,
  stepTarget,
  type GuideState,
  type SetupFacts,
} from "./guide-model";

/** Live engine facts for gating, plus the callbacks each step drives. */
export type SetupGuideProps = {
  api: EngineApi;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Open the Settings dialog on a tab (setup adds a profile, network joins). */
  onOpenSettings: (tab: "setup" | "network") => void;
  /** Start a new session (the first-agent step). */
  onStartSession: () => void;
};

/**
 * First-run walkthrough: step-by-step, skippable, resumable. Progress lives in
 * localStorage under STORAGE_KEY; every gate reads real engine data, and each
 * step's action opens the same surfaces the menus use — no fake endpoints.
 */
export function SetupGuide({
  api,
  open,
  onOpenChange,
  onOpenSettings,
  onStartSession,
}: SetupGuideProps) {
  const [state, setState] = useState<GuideState>(() => {
    if (typeof window === "undefined") return loadGuideState(null);
    return loadGuideState(window.localStorage.getItem(STORAGE_KEY));
  });
  const [facts, setFacts] = useState<SetupFacts>(emptyFacts);

  // Persist every transition; a reload resumes where the user left off.
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(STORAGE_KEY, saveGuideState(state));
  }, [state]);

  // Re-read engine facts while shown so completed steps disappear live.
  useEffect(() => {
    if (!open) return;
    let mounted = true;
    (async () => {
      const [profiles, sessions, agents] = await Promise.all([
        api.listProfiles().catch(() => ({ profiles: [], active: undefined })),
        api
          .listSessions()
          .catch(() => null)
          .then((result) => result ?? null),
        api.listAgentPool().catch(() => ({ agents: [] })),
      ]);
      if (!mounted) return;
      setFacts({
        profileCount: (profiles.profiles || []).length,
        activeProfile: profiles.active ?? null,
        anyAgentActive: (agents.agents || []).some((agent) => agent.active),
        networkLive: Boolean(
          sessions?.network?.live ||
            (sessions?.network?.remote || []).some(
              (agent) => agent.state !== "offline",
            ) ||
            (sessions?.network?.networks || []).some((group) => group.live),
        ),
      });
    })();
    return () => {
      mounted = false;
    };
  }, [api, open]);

  const { step, skipped } = useMemo(
    () => resolveStep(state, facts),
    [state, facts],
  );
  const current = SETUP_STEPS[stepIndex(step)];
  const target = stepTarget(step);

  const advance = useCallback(() => {
    setState((prev) => ({ ...prev, step: nextStep(prev.step) }));
  }, []);

  const dismiss = useCallback(() => {
    setState((prev) => ({ ...prev, dismissed: true }));
    onOpenChange(false);
  }, [onOpenChange]);

  const runTarget = useCallback(() => {
    if (!target) return;
    if (target === "settings:setup") onOpenSettings("setup");
    else if (target === "settings:network") onOpenSettings("network");
    else onStartSession();
    // Opening the surface counts as engaging the step; move on so a reopen
    // resumes at what is left.
    advance();
  }, [target, onOpenSettings, onStartSession, advance]);

  if (open && state.dismissed) {
    // A dismissed guide asked to reopen (menu entry): resume, do not reset.
    setState((prev) => ({ ...prev, dismissed: false }));
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) dismiss();
      }}
    >
      <DialogContent className="flex h-[min(30rem,80dvh)] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-xl">
        <div className="flex shrink-0 items-start justify-between gap-2 px-6 pt-5">
          <div className="min-w-0">
            <DialogTitle className="text-lg leading-none font-semibold">
              {current.title}
            </DialogTitle>
            <DialogDescription className="mt-1.5 break-words">
              {current.blurb}
            </DialogDescription>
          </div>
          <Button
            type="button"
            size="xs"
            variant="ghost"
            aria-label="Skip setup"
            onClick={dismiss}
          >
            <X />
          </Button>
        </div>
        <div className="flex min-h-0 flex-1 flex-col justify-between gap-4 px-6 py-4">
          <ol className="flex min-h-0 flex-col gap-1.5 overflow-y-auto" aria-label="Setup steps">
            {SETUP_STEPS.filter((item) => item.id !== "done").map((item) => {
              const position = stepIndex(item.id);
              const here = position === stepIndex(step);
              const complete =
                position < stepIndex(step) || skipped.includes(item.id);
              return (
                <li
                  key={item.id}
                  aria-current={here ? "step" : undefined}
                  className={cn(
                    "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm",
                    here && "bg-muted font-medium",
                    complete && !here && "text-muted-foreground",
                  )}
                  data-testid={`setup-step-${item.id}`}
                >
                {complete ? (
                  <CheckCircle2 className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
                ) : (
                  <span
                    aria-hidden="true"
                    className={cn(
                      "size-2 shrink-0 rounded-full",
                      here ? "bg-primary" : "bg-muted-foreground/40",
                    )}
                  />
                )}
                <span className="min-w-0 flex-1 truncate">{item.title}</span>
              </li>
            );
          })}
          </ol>
        </div>
        <footer className="flex shrink-0 items-center justify-between gap-2 border-t pt-3">
          <Button type="button" variant="ghost" onClick={dismiss}>
            Skip
          </Button>
          <Button
            type="button"
            data-testid="setup-guide-next"
            onClick={target ? runTarget : advance}
          >
            {target ? "Open" : "Finish"}
            <ChevronRight />
          </Button>
        </footer>
      </DialogContent>
    </Dialog>
  );
}
