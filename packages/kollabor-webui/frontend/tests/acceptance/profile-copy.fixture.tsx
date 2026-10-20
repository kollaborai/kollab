import { createRoot } from "react-dom/client";
import type { Profile } from "../../src/api";
import { ProfilesDialog } from "../../src/components/shell/ProfilesDialog";
import "../../src/styles.css";

declare global {
  interface Window {
    profileCopyCreateCalls: number;
    profileCopyLastCreate?: unknown;
  }
}

// This fixture is intentionally synthetic: it never starts or contacts an engine.
document.documentElement.classList.add("dark");
window.profileCopyCreateCalls = 0;

const sourceProfile: Profile = {
  name: "demo-work",
  provider: "openai_responses",
  model: "gpt-5.6-luna",
  description: "Synthetic profile for browser coverage",
  temperature: 0.7,
  effort: "high",
  streaming: true,
  supports_tools: true,
};

const fixtureApi = {
  listProfiles: async () => ({ providers: ["openai_responses", "anthropic"] }),
  createProfile: async (body) => {
    window.profileCopyCreateCalls += 1;
    window.profileCopyLastCreate = body;
    return { created: true };
  },
  updateProfile: async () => ({ updated: true }),
  deleteProfile: async () => ({ deleted: true }),
  testProfile: async () => ({ success: true, message: "fixture connection" }),
} satisfies Parameters<typeof ProfilesDialog>[0]["api"];

createRoot(document.getElementById("root")!).render(
  <ProfilesDialog
    api={fixtureApi}
    profiles={[sourceProfile]}
    open={true}
    onOpenChange={() => {}}
    onSaved={() => {}}
  />,
);
