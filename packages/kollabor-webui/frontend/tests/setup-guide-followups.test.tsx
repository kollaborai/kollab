import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { SetupGuide } from "../src/setup/SetupGuide";
import type { EngineApi } from "@/api";
import type { PanelWizard } from "@/api";
import { WizardSteps } from "../src/components/panels/WizardSteps";

// Follow-ups from the #1099 review, all on the guide's chrome:
//   #1116 completed steps painted emerald - a second accent hue; the app's
//        accent token is text-highlight.
//   #1117 two X controls stacked top-right - fixed on main by #1097 (the
//        DialogContent default close is suppressed); pinned here so it stays.
//   #1118 the footer primary CTA carried a trailing ChevronRight, against
//        the CTA rule (no trailing icons on primary actions).
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

// Static rendering never runs effects, so facts stay empty and nothing
// auto-skips. Seed the persisted step at network: every earlier step renders
// its completion mark, which is where #1116's hue shows.
vi.mock("../src/setup/guide-model", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/setup/guide-model")>();
  return {
    ...actual,
    loadGuideState: () => ({ step: "network", dismissed: false }),
  };
});

// Never called in static rendering (effects do not run); the stub only keeps
// the EngineApi prop typed.
const api = {
  listProfiles: async () => ({ profiles: [], active: undefined }),
  listSessions: async () => null,
  listAgentPool: async () => ({ agents: [] }),
} as unknown as EngineApi;

function guideMarkup(open: boolean) {
  return renderToStaticMarkup(
    <SetupGuide
      api={api}
      open={open}
      onOpenChange={() => {}}
      onOpenSettings={() => {}}
      onStartSession={() => {}}
    />,
  );
}

describe("guided setup review follow-ups (#1116/#1117/#1118)", () => {
  it("#1116 completed steps use the highlight accent, not a second hue", () => {
    const markup = guideMarkup(true);
    // A completed step's check mark rendered before the fix.
    expect(markup).toContain("text-highlight");
    expect(markup).not.toContain("text-emerald");
  });

  it("#1117 keeps exactly one close control (fixed on main by #1097)", () => {
    const markup = guideMarkup(true);
    expect(markup).toContain('aria-label="Skip setup"');
    expect(markup).not.toContain('data-slot="dialog-close"');
    expect(markup).not.toContain("sr-only");
  });

  it("#1118 the footer CTA carries no trailing chevron", () => {
    const markup = guideMarkup(true);
    expect(markup).not.toContain("lucide-chevron-right");
  });
});

describe("ChatGPT setup sign-in step", () => {
  const panel = {
    panel: "setup",
    kind: "wizard",
    title: "Setup",
    context: {
      oauth_flow_id: "flow-123",
      verification_url: "https://auth.openai.com/codex/device",
      user_code: "ABCD-EFGH",
      oauth_status: "waiting",
      account: "",
    },
    steps: [
      {
        id: "provider",
        title: "Provider",
        fields: [
          {
            path: "provider",
            type: "dropdown",
            label: "Provider",
            value: "OpenAI (sign in with ChatGPT)",
            options: ["OpenAI (API key)", "OpenAI (sign in with ChatGPT)"],
          },
        ],
      },
      { id: "api_key", title: "API Key", fields: [] },
      { id: "endpoint", title: "Server", fields: [] },
      { id: "oauth_sign_in", title: "Sign In", fields: [] },
      { id: "model", title: "Model", fields: [] },
      { id: "oauth_review", title: "Review", fields: [] },
    ],
    actions: [{ id: "finish", label: "Save and Activate" }],
  } as unknown as PanelWizard;

  it("skips API Key and Server and shows the device code and actions", () => {
    const markup = renderToStaticMarkup(
      <WizardSteps
        panel={panel}
        draft={{ provider: "OpenAI (sign in with ChatGPT)" }}
        step={1}
        errors={{}}
        busy={false}
        onDraftChange={() => {}}
        onStepChange={() => {}}
        onAction={() => {}}
      />,
    );

    expect(markup).toContain("Step 2 of 4: Sign In");
    expect(markup).toContain("https://auth.openai.com/codex/device");
    expect(markup).toContain("ABCD-EFGH");
    expect(markup).toContain("Open Page");
    expect(markup).toContain("Copy");
    expect(markup).toContain("Waiting for sign-in");
    expect(markup).toContain("Cancel");
    expect(markup).not.toContain("API Key");
    expect(markup).not.toContain("Server Address");
  });
});
