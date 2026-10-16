// Pure model for the guided setup walkthrough: which step comes next, when a
// step is satisfied by real engine data, and how progress persists. No React,
// no fetch — everything here is testable with plain data.

export type SetupStepId =
  | "welcome"
  | "provider"
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
    id: "provider",
    title: "Choose a provider",
    blurb: "Pick how Kollab talks to a model. Skip to keep the default.",
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
  provider: 1,
  network: 2,
  agent: 3,
  done: 4,
};

export const STORAGE_KEY = "kollab.setup-guide.v1";

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
    const step =
      typeof parsed.step === "string" && parsed.step in STEP_INDEX
        ? (parsed.step as SetupStepId)
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
  /** Named profiles from GET /profiles (the provider step's concern). */
  profileCount: number;
  /** The engine's active profile name, if any. */
  activeProfile: string | null;
  /** At least one agent is live right now (the agent step's concern). */
  anyAgentActive: boolean;
  /** The device carries at least one network with live agents (#158 shape). */
  networkLive: boolean;
};

export function emptyFacts(): SetupFacts {
  return {
    profileCount: 0,
    activeProfile: null,
    anyAgentActive: false,
    networkLive: false,
  };
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
    if (step === "provider" && facts.profileCount > 0) {
      skipped.push(step);
      step = nextStep(step);
      continue;
    }
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
): "settings:setup" | "settings:network" | "session:new" | null {
  switch (step) {
    case "provider":
      return "settings:setup";
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
