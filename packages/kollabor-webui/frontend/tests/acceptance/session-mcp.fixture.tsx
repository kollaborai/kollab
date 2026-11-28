import { createRoot } from "react-dom/client";
import { EngineApi, type Session } from "../../src/api";
import { SessionPanel, type SessionView } from "../../src/components/shell/SessionPanel";
import "../../src/styles.css";

const api = new EngineApi(window.location.origin);
const session: Session = {
  session_id: "session-a",
  name: "Session A",
  workspace: "/mcp-demo",
  identity: "fixture-agent",
  agent: "coder",
  profile: "default",
  approval_mode: "ask",
};

function Fixture() {
  const [activeSession, setActiveSession] = React.useState(session);
  const [view, setView] = React.useState<SessionView>("chat");
  const [sheetOpen, setSheetOpen] = React.useState(false);

  return (
    <main className="flex h-screen bg-background p-4 text-foreground">
      <button
        type="button"
        data-testid="switch-session"
        onClick={() => setActiveSession({ ...session, session_id: "session-b", name: "Session B" })}
      >
        Switch session
      </button>
      <SessionPanel
        api={api}
        session={activeSession}
        profiles={[{ name: "default", provider: "test", model: "fixture-model" }]}
        pool={[]}
        onStatus={() => undefined}
        onSessionUpdated={() => undefined}
        onOpenSettings={() => undefined}
        onHistoryCleared={async () => undefined}
        view={view}
        onViewChange={setView}
        sheetOpen={sheetOpen}
        onSheetOpenChange={setSheetOpen}
      />
    </main>
  );
}

import React from "react";
createRoot(document.getElementById("root")!).render(<Fixture />);
