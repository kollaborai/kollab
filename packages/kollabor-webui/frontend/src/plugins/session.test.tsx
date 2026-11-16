import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PluginSessionControls } from "./session";

it("renders the declared kebab-case menu and view icons", () => {
  const snapshot = { errors: {}, renderFailed: [], entries: [{ id: "hello", name: "Hello", enabled: true }],
    registry: { plugins: [{ id: "hello", views: [{ id: "view", label: "View", icon: "box" }],
      menus: [{ id: "page", label: "Page", icon: "git-pull-request", open: { page: "page" } }],
      pages: [], modals: [] }] } };
  const host = { subscribe: () => () => {}, getSnapshot: () => snapshot,
    getContext: () => ({}), engineTime: () => 0 };
  const html = renderToStaticMarkup(<PluginSessionControls host={host as never} session={{ workspace: "/work" } as never}
    view="chat" onViewChange={() => {}} onNavigate={() => {}} />);
  expect(html).toContain("lucide-box");
  expect(html).toContain("lucide-git-pull-request");
});
