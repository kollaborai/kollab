// Pure model for the guided setup walkthrough: which step comes next, when a
// step is satisfied by real engine data, and how progress persists. No React,
// no fetch — everything here is testable with plain data.

export type SetupStepId =
  | "welcome"
  | "network"
  | "agent"
  | "done";

export type SetupStep = {
  id: SetupStepId;
  title: string;
  /** One short line under the title. */
  blurb: string;
};

export const SETUP_STEPS: readonly SetupStep[] = [
  {
    id: "welcome",
    title: "Welcome to Kollab",
    blurb: "Connect a model, join a network, and meet your first agent.",
  },
  {
    id: "network",
    title: "Join a network",
    blurb: "Optional: link this device to your agent network.",
  },
  {
    id: "agent",
    title: "Start your first agent",
    blurb: "Pick a gem from the pool and open its chat.",
  },
  {
    id: "done",
    title: "You're set",
    blurb: "Everything is optional from here — enjoy.",
  },
];

const STEP_INDEX: Record<SetupStepId, number> = {
  welcome: 0,
  network: 1,
  agent: 2,
  done: 3,
};

export const STORAGE_KEY = "kollab.setup-guide.v1";
export const WELCOME_GUIDE_DISMISSED_NOTICE =
  "Welcome guide closed. Type /welcome in a chat to open it again.";

export type GuideState = {
  step: SetupStepId;
  /** True once the user dismissed the guide (per browser). */
  dismissed: boolean;
};

export function initialGuideState(): GuideState {
  return { step: "welcome", dismissed: false };
}

/** Read persisted progress; malformed or foreign data falls back to a fresh start. */
export function loadGuideState(raw: string | null): GuideState {
  if (!raw) return initialGuideState();
  try {
    const parsed = JSON.parse(raw) as {
      step?: unknown;
      dismissed?: unknown;
    };
    // "provider" was a card until #1217. Resume that saved state at the
    // welcome card, as requested by the issue.
    const saved = parsed.step === "provider" ? "welcome" : parsed.step;
    const step =
      typeof saved === "string" && saved in STEP_INDEX
        ? (saved as SetupStepId)
        : "welcome";
    return { step, dismissed: parsed.dismissed === true };
  } catch {
    return initialGuideState();
  }
}

export function saveGuideState(state: GuideState): string {
  return JSON.stringify({ step: state.step, dismissed: state.dismissed });
}

export function stepIndex(step: SetupStepId): number {
  return STEP_INDEX[step];
}

export function nextStep(step: SetupStepId): SetupStepId {
  return SETUP_STEPS[Math.min(stepIndex(step) + 1, SETUP_STEPS.length - 1)].id;
}

export function prevStep(step: SetupStepId): SetupStepId {
  return SETUP_STEPS[Math.max(stepIndex(step) - 1, 0)].id;
}

/** Everything the steps gate on, fetched by the caller from real APIs. */
export type SetupFacts = {
  /**
   * The active profile runs a real provider (see hasChosenProvider). Settings >
   * Setup is the only provider screen; this decides whether "Get Started" opens it.
   */
  providerChosen: boolean;
  /** The engine's active profile name, if any. */
  activeProfile: string | null;
  /** At least one agent is live right now (the agent step's concern). */
  anyAgentActive: boolean;
  /** The device carries at least one network with live agents (#158 shape). */
  networkLive: boolean;
};

export function emptyFacts(): SetupFacts {
  return {
    providerChosen: false,
    activeProfile: null,
    anyAgentActive: false,
    networkLive: false,
  };
}

/**
 * True when the active profile runs a real provider. The engine always lists its
 * built-in profiles, and the active one on a fresh install is "default" with
 * provider "auto" (resolved from env vars at run time), which is no choice at
 * all, so a profile merely existing must not count (#1217).
 */
export function hasChosenProvider(
  profiles: readonly { name: string; provider?: string | null }[],
  active?: string | null,
): boolean {
  const current = profiles.find((profile) => profile.name === active);
  return Boolean(current?.provider && current.provider !== "auto");
}

/** "Get Started" opens Settings > Setup, the one provider screen, until a provider is chosen. */
export function getStartedOpensSetup(facts: SetupFacts): boolean {
  return !facts.providerChosen;
}

/**
 * Derive the step to show from persisted progress and current facts. A step
 * whose goal is already true auto-skips forward (welcome never skips: it is
 * the greeting). Dismissed guides only come back when the caller forces it.
 */
export function resolveStep(state: GuideState, facts: SetupFacts): {
  step: SetupStepId;
  skipped: SetupStepId[];
} {
  if (state.dismissed) return { step: "done", skipped: [] };
  const skipped: SetupStepId[] = [];
  let step = state.step;
  for (let guard = 0; guard < SETUP_STEPS.length; guard += 1) {
    if (step === "welcome" || step === "done") break;
    if (step === "network" && facts.networkLive) {
      skipped.push(step);
      step = nextStep(step);
      continue;
    }
    if (step === "agent" && facts.anyAgentActive) {
      skipped.push(step);
      step = nextStep(step);
      continue;
    }
    break;
  }
  return { step, skipped };
}

/** Which concrete panel/route each step's action should open. */
export function stepTarget(
  step: SetupStepId,
): "settings:network" | "session:new" | null {
  switch (step) {
    case "network":
      return "settings:network";
    case "agent":
      return "session:new";
    default:
      return null;
  }
}

export const isSetupStepId = (value: string): value is SetupStepId =>
  value in STEP_INDEX;
