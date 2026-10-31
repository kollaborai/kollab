import { describe, expect, it } from "vitest";
import {
  SIDEBAR_BREAKPOINT,
  sidebarIsDrawer,
} from "../src/hooks/use-mobile";

describe("sidebar drawer breakpoint (#458)", () => {
  it("uses drawers at 820px, where both sidebars squeezed the chat", () => {
    expect(sidebarIsDrawer(820)).toBe(true);
  });

  it("keeps both sidebars inline at 1440px", () => {
    expect(sidebarIsDrawer(1440)).toBe(false);
  });

  it("switches exactly at the breakpoint: 1023 draws, 1024 stays inline", () => {
    expect(sidebarIsDrawer(SIDEBAR_BREAKPOINT - 1)).toBe(true);
    expect(sidebarIsDrawer(SIDEBAR_BREAKPOINT)).toBe(false);
  });

  it("keeps 390px phones on the drawer treatment", () => {
    expect(sidebarIsDrawer(390)).toBe(true);
  });
});
