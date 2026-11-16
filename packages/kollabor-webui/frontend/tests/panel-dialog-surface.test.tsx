import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { PanelDialogSurface } from "../src/components/panels/PanelDialogSurface";

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
