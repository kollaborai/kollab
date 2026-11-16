import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { SetupGuide } from "../src/setup/SetupGuide";
import type { EngineApi } from "@/api";

// Radix portals never mount in static rendering, so the real dialog primitives
// are replaced with passthroughs. The project's own DialogContent wrapper (and
// its showCloseButton branch) still runs, which is exactly what issue #1091
// exercises: its default close button used to stack on top of the guide's
// header skip button, showing two X controls.
vi.mock("radix-ui", async () => {
  const React = await import("react");
  const passthrough = ({ children }: { children?: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children);
  // Radix Root gates the portal on open; model that so a closed dialog
  // renders nothing.
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

describe("guided setup dialog close controls", () => {
  it("renders exactly one close control: the header skip button", () => {
    const markup = guideMarkup(true);

    // The guide's own header X.
    expect(markup).toContain('aria-label="Skip setup"');
    // DialogContent's default close button must be suppressed: its only
    // markup footprint is the dialog-close slot and the sr-only label.
    expect(markup).not.toContain('data-slot="dialog-close"');
    expect(markup).not.toContain("sr-only");
  });

  it("renders nothing when closed", () => {
    expect(guideMarkup(false)).toBe("");
  });

  it("sizes the panel to its content and caps it to the viewport", () => {
    const markup = guideMarkup(true);

    expect(markup).toContain("max-h-[80dvh]");
    expect(markup).not.toContain("h-[min(30rem,80dvh)]");
  });
});
