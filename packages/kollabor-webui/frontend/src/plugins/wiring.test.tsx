import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import type { EngineApi, Session } from "@/api";
import { TooltipProvider } from "@/components/ui/tooltip";
import { SessionPanel } from "@/components/shell/SessionPanel";
import { EMPTY_CONTRIBUTIONS, pluginRegistry } from "./registry";
import type { PluginApi } from "./sdk";

// Effects never run in static rendering, so the engine is never called.
const api = {} as EngineApi;
const session = { session_id: "s1", name: "chat", identity: "lapis", workspace: "/work", approval_mode: "default" } as Session;
const Body = () => null;

function panel(workspace = "/work") {
  return renderToStaticMarkup(
    <TooltipProvider>
      <SessionPanel
        api={api}
        session={{ ...session, workspace }}
        profiles={[]}
        pool={[]}
        onStatus={() => {}}
        onSessionUpdated={() => {}}
        onOpenSettings={() => {}}
        onHistoryCleared={async () => {}}
        view="chat"
        onViewChange={() => {}}
        sheetOpen={false}
        onSheetOpenChange={() => {}}
      />
    </TooltipProvider>,
  );
}

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

function putHello() {
  pluginRegistry.put({
    id: "hello",
    name: "Hello",
    entry: {
      id: "hello",
      name: "Hello",
      kind: "installed",
      enabled: true,
      shadowed: false,
      state: "ok",
      error: null,
      web: { entry: "web/dist/index.js", hash: "abc" },
      contributes: { menu: [{ id: "hello", label: "Hello Row", icon: "hand", open: { page: "main" } }] },
      services: [],
      omitted: { services: 0, uses: 0, granted: 0, settings: 0, setting_options: 0, option_labels: 0, contributions: 0 },
    },
    status: "ready",
    error: null,
    retries: 0,
    k: { id: "hello" } as unknown as PluginApi,
    contributions: {
      ...EMPTY_CONTRIBUTIONS,
      views: [
        { id: "peek", label: "Peek", render: Body },
        { id: "files", label: "Files", render: Body, needs_folder: true },
      ],
    },
  });
}

afterEach(() => pluginRegistry.reset());

describe("SessionPanel with plugins", () => {
  it("with no plugins it shows only kollab's own views and rows", () => {
    const shown = text(panel());
    expect(shown).toContain("Chat Trajectory Code Editor Git Terminal Gallery");
    expect(shown).toMatch(/MCP Servers .*Who Is Online .*Settings Clear History/);
    expect(panel()).not.toContain("data-plugin");
  });

  it("puts plugin rows after Who Is Online and before Settings", () => {
    putHello();
    const shown = text(panel());
    expect(shown).toMatch(/Who Is Online [^A-Z]*Hello Row Settings/);
  });

  it("lists plugin views after Gallery, and hides one that needs a folder in a chat with none", () => {
    putHello();
    expect(text(panel())).toContain("Gallery Peek Files");
    expect(panel()).toContain('data-testid="plugin:hello:peek-tab"');
    expect(text(panel(""))).toContain("Gallery Peek");
    expect(text(panel(""))).not.toContain("Files");
  });
});
