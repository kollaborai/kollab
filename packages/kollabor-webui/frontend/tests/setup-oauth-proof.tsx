import { createRoot } from "react-dom/client";
import type { PanelWizard } from "@/api";
import { WizardSteps } from "../src/components/panels/WizardSteps";
import "../src/styles.css";

const query = new URLSearchParams(location.search);
const status = query.get("status") ?? "waiting";
const theme = query.get("theme") ?? "light";
document.documentElement.classList.toggle("dark", theme === "dark");

const panel = {
  panel: "setup",
  kind: "wizard",
  title: "Setup",
  context: {
    oauth_flow_id: "proof-flow",
    verification_url: "https://auth.openai.com/codex/device",
    user_code: "ABCD-EFGH",
    oauth_status: status,
    oauth_message: status === "network_error" ? "Network error while signing in. Retrying…" : "",
    account: status === "signed_in" ? "account-123" : "",
  },
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
          options: ["OpenAI (API key)", "OpenAI (sign in with ChatGPT)"],
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
} as unknown as PanelWizard;

createRoot(document.getElementById("root")!).render(
  <main className="min-h-dvh bg-background px-4 py-6 text-foreground sm:px-8">
    <div className="mx-auto flex min-h-[30rem] w-full max-w-xl flex-col bg-background">
      <h1 className="px-4 pt-4 text-lg font-semibold">Setup</h1>
      <WizardSteps
        panel={panel}
        draft={{ provider: "OpenAI (sign in with ChatGPT)" }}
        step={1}
        errors={{}}
        busy={false}
        onDraftChange={() => {}}
        onStepChange={() => {}}
        onAction={() => {}}
      />
    </div>
  </main>,
);
