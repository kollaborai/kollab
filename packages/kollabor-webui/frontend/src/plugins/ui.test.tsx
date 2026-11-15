import { describe, expect, it } from "vitest";
import { Button } from "@/components/ui/button";
import { pluginUI } from "./ui";

describe("plugin design-system primitives", () => {
  it("exposes the host Button and the documented common primitives", () => {
    expect(pluginUI.Button).toBe(Button);
    for (const name of ["Alert", "Table", "Tabs", "Dialog", "Badge", "Card", "Input", "Select"]) {
      expect(pluginUI).toHaveProperty(name);
      expect(pluginUI[name as keyof typeof pluginUI]).toBeTruthy();
    }
  });
});
