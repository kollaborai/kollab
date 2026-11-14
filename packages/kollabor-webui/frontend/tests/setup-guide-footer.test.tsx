import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { SetupGuide } from "../src/setup/SetupGuide";
import type { EngineApi } from "@/api";
import type { SetupStepId } from "../src/setup/guide-model";

// The guide seeds its step from localStorage; in the node test environment
// window is undefined, so loadGuideState never sees real storage. The mock
// pins the persisted step per test while everything else in guide-model
// stays real (stepTarget, resolveStep, SETUP_STEPS).
const persisted = vi.hoisted(() => ({ step: "done" as SetupStepId }));

vi.mock("../src/setup/guide-model", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/setup/guide-model")>();
  return {
    ...actual,
    loadGuideState: () => ({ step: persisted.step, dismissed: false }),
  };
});

// Radix portals never mount in static rendering, so the dialog primitives are
// replaced with passthroughs (Root still gates on open).
vi.mock("radix-ui", async () => {
  const React = await import("react");
  const passthrough = ({ children }: { children?: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children);
  const root = ({ open, children }: { open?: boolean; children?: React.ReactNode }) =>
    open ? React.createElement(React.Fragment, null, children) : null;
  const dialog = {
    Root: root,
    Trigger: passthrough,
    Portal: passthrough,
    Overlay: passthrough,
    Content: passthrough,
    Close: passthrough,
    Title: passthrough,
    Description: passthrough,
  };
  return { Dialog: dialog };
});

const api = {
  listProfiles: async () => ({ profiles: [], active: undefined }),
  listSessions: async () => null,
  listAgentPool: async () => ({ agents: [] }),
} as unknown as EngineApi;

function guideMarkup(step: SetupStepId) {
  persisted.step = step;
  return renderToStaticMarkup(
    <SetupGuide
      api={api}
      open
      onOpenChange={() => {}}
      onOpenSettings={() => {}}
      onStartSession={() => {}}
    />,
  );
}

// The footer Skip button renders exactly ">Skip</button>"; the header skip
// control carries aria-label="Skip setup" and never matches that text run.
describe("guided setup footer", () => {
  it("final step shows Finish and no Skip", () => {
    const markup = guideMarkup("done");
    expect(markup).toContain(">Finish<");
    expect(markup).not.toContain(">Skip</button>");
    expect(markup).not.toContain(">Open<");
  });

  it("actionable step keeps Skip next to Open", () => {
    const markup = guideMarkup("agent");
    expect(markup).toContain(">Skip</button>");
    expect(markup).toContain(">Open<");
    expect(markup).not.toContain(">Finish<");
  });

  it("welcome has no target, so no footer Skip", () => {
    const markup = guideMarkup("welcome");
    expect(markup).not.toContain(">Skip</button>");
    expect(markup).toContain(">Finish<");
  });
});
