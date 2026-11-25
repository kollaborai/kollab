import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { PanelForm } from "../src/api";
import { PanelDialogSurface } from "../src/components/panels/PanelDialogSurface";
import { FormPanel } from "../src/components/panels/FormPanel";

vi.mock("@/components/ui/dialog", async () => {
  const React = await import("react");
  return {
    DialogContent: ({ className, children }: { className?: string; children: React.ReactNode }) =>
      React.createElement("div", { className }, children),
  };
});

describe("settings dialog surface", () => {
  it("uses the neutral surface without a decorative metal rim", () => {
    const markup = renderToStaticMarkup(
      <PanelDialogSurface>
        <span>settings content</span>
      </PanelDialogSurface>,
    );

    expect(markup).toContain("bg-background");
    expect(markup).not.toContain("LiquidMetalBorder");
    expect(markup).not.toContain("linear-gradient");
  });
});

describe("form panel notice", () => {
  it("renders an actionable configuration warning for assistive technology", () => {
    const notice = "The global configuration is malformed. Back it up and repair the JSON before saving.";
    const panel: PanelForm = {
      panel: "config",
      kind: "form",
      title: "Settings",
      notice,
      sections: [],
    };
    const markup = renderToStaticMarkup(
      <FormPanel
        panel={panel}
        draft={{}}
        errors={{}}
        busy={false}
        onDraftChange={() => undefined}
        onAction={() => undefined}
      />,
    );

    expect(markup).toContain('role="alert"');
    expect(markup).toContain(notice);
  });
});
