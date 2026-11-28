import { createRoot } from "react-dom/client";
import type { EngineApi, Session } from "../src/api";
import { SessionPanel } from "../src/components/shell/SessionPanel";
import "../src/styles.css";

const serverName = "production-mcp-server-with-a-very-long-name-that-keeps-going-across-the-phone-screen";
const api = {
  getSessionMcp: async () => ({ servers: { [serverName]: { status: "connected", tools: [], tool_count: 0 } } }),
  listMcpServers: async () => ({ servers: { [serverName]: { command: "mcp-server" } } }),
  listHubAgents: async () => ({ agents: [] }),
} as unknown as EngineApi;
const session = {
  session_id: "layout-proof-session",
  name: "Layout proof",
  agent: "coder",
  identity: "coder",
  profile: "default",
  workspace: "/workspace",
  external: false,
} as Session;

createRoot(document.getElementById("root")!).render(
  <main className="flex min-h-screen w-full items-start justify-center bg-background p-4 text-foreground">
    <div className="w-full max-w-sm">
      <SessionPanel
        api={api}
        session={session}
        profiles={[]}
        pool={[]}
        onStatus={() => {}}
        onSessionUpdated={() => {}}
        onOpenSettings={() => {}}
        onHistoryCleared={async () => {}}
        view="chat"
        onViewChange={() => {}}
        sheetOpen
        onSheetOpenChange={() => {}}
      />
    </div>
  </main>,
);
