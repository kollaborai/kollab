import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { SetupGuide } from "../src/setup/SetupGuide";
import { DEFAULT_SLASH_COMMANDS } from "../src/api";
import { WELCOME_GUIDE_DISMISSED_NOTICE } from "../src/setup/guide-model";
import type { EngineApi } from "@/api";

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


describe("welcome guide recovery entry", () => {
  it("gives the person the exact command to reopen a dismissed guide", () => {
    expect(WELCOME_GUIDE_DISMISSED_NOTICE).toBe(
      "Welcome guide closed. Type /welcome in a chat to open it again.",
    );
  });

  it("lists /welcome with its exact slash palette description", () => {
    expect(DEFAULT_SLASH_COMMANDS).toContainEqual(
      expect.objectContaining({
        name: "welcome",
        description: "Show the welcome guide again",
      }),
    );
  });
});
