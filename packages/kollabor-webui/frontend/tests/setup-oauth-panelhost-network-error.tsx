import { createRoot } from "react-dom/client";
import { PanelHost } from "../src/components/panels/PanelHost";
import "../src/styles.css";

const setupPanel = (context = {}) => ({
  panel: "setup",
  kind: "wizard",
  title: "Setup",
  context,
  steps: [
    {
      id: "provider",
      title: "Provider",
      fields: [
        {
          path: "provider",
          type: "dropdown",
          label: "Provider",
          value: "OpenAI (sign in with ChatGPT)",
          options: ["OpenAI (sign in with ChatGPT)"],
        },
      ],
    },
    { id: "api_key", title: "API Key", fields: [] },
    { id: "endpoint", title: "Server", fields: [] },
    {
      id: "oauth_sign_in",
      title: "Sign In",
      fields: [],
      actions: [
        { id: "oauth_start", label: "Try Again" },
        { id: "oauth_cancel", label: "Cancel" },
      ],
    },
    { id: "model", title: "Model", fields: [] },
    { id: "oauth_review", title: "Review", fields: [] },
  ],
  actions: [{ id: "finish", label: "Save and Activate" }],
});

const api = {
  loadConfig: async () => ({}),
  getSessionState: async () => ({}),
  getPanel: async () => setupPanel(),
  panelAction: async (_session: string, _panel: string, action: string) => {
    if (action === "oauth_start") {
      return {
        ok: true,
        panel: setupPanel({
          oauth_flow_id: "flow",
          user_code: "ABCD-EFGH",
          oauth_status: "waiting",
          oauth_message: "Waiting for sign-in…",
        }),
        poll: {
          action: "oauth_poll",
          payload: { flow_id: "flow" },
          every_s: 0.5,
        },
      };
    }
    if (action === "oauth_poll") {
      return {
        ok: false,
        message: "Network error while signing in. Retrying…",
        panel: setupPanel({
          oauth_flow_id: "flow",
          user_code: "ABCD-EFGH",
          oauth_status: "network_error",
          oauth_message: "Network error while signing in. Retrying…",
        }),
        poll: {
          action: "oauth_poll",
          payload: { flow_id: "flow" },
          every_s: 0.5,
        },
      };
    }
    return { ok: true };
  },
} as any;

createRoot(document.getElementById("root")!).render(
  <PanelHost
    api={api}
    session={{ session_id: "session" } as any}
    profiles={[]}
    open
    onOpenChange={() => {}}
    tab="setup"
    onTabChange={() => {}}
    intent={null}
    onChanged={() => {}}
    onSessionUpdated={() => {}}
  />,
);
