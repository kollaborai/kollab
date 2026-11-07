import { createRoot } from "react-dom/client";
import type { Profile } from "../../src/api";
import { ProfilesDialog } from "../../src/components/shell/ProfilesDialog";
import "../../src/styles.css";

declare global {
  interface Window {
    profileRowsTestCalls: number;
    profileRowsExpectedMeta: string[];
  }
}

// This fixture is intentionally synthetic: it never starts or contacts an engine.
// Profiles exercise the narrow-screen metadata layout from issue #578: long
// names, long model ids, endpoint and ACP target labels, and a missing effort.
window.profileRowsTestCalls = 0;
// The full meta line each row must render (provider · model · N effort), so the
// browser harness can fail when any piece goes missing instead of merely
// checking whatever text happened to render.
window.profileRowsExpectedMeta = [
  "openai_responses · gpt-5.6-luna-ultra-context-2026-preview · high effort",
  "anthropic · claude-sonnet-4-6 · medium effort",
  "acp · gemini-3-pro · ultra effort",
];

const profiles: Profile[] = [
  {
    name: "deep-research-squad-profile-with-a-very-long-name",
    provider: "openai_responses",
    model: "gpt-5.6-luna-ultra-context-2026-preview",
    description: "Synthetic long-name profile for browser coverage",
    base_url: "https://relay.kollabor.ai/v1/backend",
    temperature: 0.7,
    effort: "high",
    streaming: true,
    supports_tools: true,
  },
  {
    name: "quick",
    provider: "anthropic",
    model: "claude-sonnet-4-6",
    temperature: 0.7,
    effort: "medium",
    streaming: true,
    supports_tools: true,
  },
  {
    name: "acp-local",
    provider: "acp",
    acp_adapter: "gemini-acp-cli --very-long-adapter-argument",
    model: "gemini-3-pro",
    temperature: 0.7,
    effort: "ultra",
    streaming: true,
    supports_tools: true,
  },
];

const fixtureApi = {
  listProfiles: async () => ({ providers: ["openai_responses", "anthropic", "acp"] }),
  createProfile: async () => ({ created: true }),
  updateProfile: async () => ({ updated: true }),
  deleteProfile: async () => ({ deleted: true }),
  testProfile: async () => {
    window.profileRowsTestCalls += 1;
    return { success: true, message: "fixture connection" };
  },
} satisfies Parameters<typeof ProfilesDialog>[0]["api"];

createRoot(document.getElementById("root")!).render(
  <ProfilesDialog
    api={fixtureApi}
    profiles={profiles}
    open={true}
    onOpenChange={() => {}}
    onSaved={() => {}}
  />,
);
