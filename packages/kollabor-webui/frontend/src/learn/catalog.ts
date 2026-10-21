export type LearnTopicDefinition = {
  id: string;
  title: string;
  summary: string;
  howTo: readonly string[];
  example: string;
  note?: string;
};

/** Stable deep-link IDs. Change these only with an intentional URL migration. */
export const LEARN_TOPICS = [
  {
    id: "networks",
    title: "Kollab networks",
    summary:
      "A Kollab network lets trusted Kollab installs find one another and share agent presence. It is a Kollab peer mesh, not a VPN or a public chat room.",
    howTo: [
      "On the computer that will invite another device, open the Network settings or enter /connect in chat.",
      "Create a one-time invite code and share it only with the person who should join.",
      "Accept the invite on the other trusted Kollab install, then check that both devices appear in the sidebar.",
      "Open a device to browse its available workspaces and agents.",
    ],
    example:
      "Invite a second computer with a one-time code, then use the sidebar to reach an agent running on that computer.",
    note: "Treat invite codes as private. Do not post them in a public channel or include them in screenshots.",
  },
  {
    id: "devices",
    title: "Devices",
    summary:
      "A device is one computer running Kollab. The sidebar shows which computers are in your network and lets you browse their available work.",
    howTo: [
      "Expand Networks in the sidebar.",
      "Choose the computer you want to use.",
      "Open one of that device's workspaces, then select an agent or session.",
      "If a computer is missing, confirm that Kollab is running there and that it joined the same network.",
    ],
    example:
      "Keep a build agent on your workstation and open that device from a laptop when you want to review its work.",
  },
  {
    id: "workspaces",
    title: "Workspaces",
    summary:
      "A workspace is a project folder. Choosing a folder keeps its sessions, local agents, and project context together.",
    howTo: [
      "Use the workspace picker to select a project folder.",
      "Choose an agent that is available in that folder.",
      "Start a session; new work is created in the selected workspace.",
      "Switch folders from the same picker when you move to another project.",
    ],
    example:
      "Use one workspace for the Kollab repository and another for a customer project so their sessions and local agents stay separate.",
  },
  {
    id: "agents",
    title: "Agents",
    summary:
      "Agents are named collaborators with their own instructions and optional skills. Rosters group agents; teams can start several agents together. In the sidebar, local agents are grouped under their workspace so you can tell which project each one works in.",
    howTo: [
      "Open Agents to see the rosters and teams available in the selected workspace.",
      "Choose a single agent for a focused task, or a team when the work needs parallel roles.",

      "Agents created from the Web UI stay running through Web UI or engine restarts and return with their conversation. Use Delete in the Web UI or send /quit to the agent to stop it.",
      "Give the agent a concrete result to produce and how you will verify it.",
      "Drag an agent row to reorder it within its workspace. Rows move aside while you drag; drop to save. The order is saved by this Kollab instance and restored on refresh.",
      "Drag past the sidebar edge and release on the Release to hide cue to tuck an agent into Hidden Agents. You can restore it there; hiding does not stop or delete it.",
      "If another device changed the order at the same time, the sidebar refreshes and asks you to retry; your change is not silently lost.",
      "Review its output and tests before using the result.",
    ],
    example:
      "Start a Web UI agent on a focused task, restart the engine, and confirm the same agent and conversation return in the sidebar. Use Web UI Delete or send /quit when you want it to stop.",
  },
  {
    id: "profiles",
    title: "Provider profiles",
    summary:
      "A profile chooses the provider, base URL/endpoint, model, and reasoning effort for a session. The name is only a label; the endpoint is where requests go. A profile is separate from the agent's role and instructions.",
    howTo: [
      "Choose a saved profile; review its provider, base URL/endpoint, model, and reasoning effort before starting a session.",
      "When copying a profile, check the provider and endpoint shown in the copy form. Stored fields the form does not show, including an ACP command, are preserved.",
      "Keyring-backed credentials and sign-in profiles remain stored, but environment-sourced or provisioned keys do not move to the copy. Set its KOLLAB_<PROFILE>_API_KEY or sign in separately.",
      "Select the profile before starting a session. It does not change the chosen agent.",
    ],
    example:
      "Copy an ACP-backed profile and confirm its command remains available. If its source key came from KOLLAB_<PROFILE>_API_KEY or KOLLAB_API_KEY, set a profile-specific key for the copy or authenticate again.",
    note: "Profile names do not guarantee the provider endpoint. Verify the base URL, and never paste key values into prompts or Hub messages."
  },
  {
    id: "koordinator",
    title: "Koordinator",
    summary:
      "The koordinator keeps a multi-agent effort organized: it tracks assignments, asks for progress, and routes work to the right agent.",
    howTo: [
      "Start or join the hub that owns the work.",
      "Give the koordinator a bounded goal, acceptance checks, and any no-go constraints.",
      "Check the task board and ask for a report when an assignment finishes.",
      "Review the delivered changes and test evidence yourself before accepting them.",
    ],
    example:
      "Split a feature into one UI task and one test task, then ask the koordinator to report both results and the integration check.",
    note: "A coordinator organizes work; it does not replace review or make an untested change safe to ship.",
  },
  {
    id: "hub-messaging",
    title: "Hub messaging",
    summary:
      "Hub messages coordinate agents across a shared channel. Messages are visible to the hub, so they are not private direct messages.",
    howTo: [
      "Address a message to the agent's identity, not its general agent type.",
      "State the task, relevant context, and the evidence you want back.",
      "Use the shared channel for coordination that other hub members should see.",
      "Never send passwords, API keys, private keys, or one-time access codes in hub chat.",
    ],
    example:
      'Send `hub_msg to="lapis"` with a short request such as “check the Learn links and report any broken routes.”',
    note: "Hub messages are visible to other members; do not assume a message is private.",
  },
  {
    id: "voice",
    title: "Voice mode",
    summary:
      "Voice mode lets you speak to the active chat. The microphone is not enabled just by opening Kollab; you turn voice mode on explicitly.",
    howTo: [
      "Open the Voice controls or enter /voice status to check the current state.",
      "Enter /voice on when you are ready to speak.",
      "Talk to the active chat and wait for its response.",
      "Enter /voice off when you are done to release the microphone.",
    ],
    example:
      "Say “summarize the last answer in three steps,” then use /voice off when the conversation is finished.",
    note: "Opening Learn never turns on the microphone. Use /voice status if you are unsure whether voice is active. Separately, /config > Hub > Dream Model picks a cheaper model ID (supported by your active provider) for background agent dreaming only; blank falls back to the shared kollabor.llm.fast_model, then the active session model, reusing your active profile's endpoint and credentials. Dream Model does not change voice replies: the fast voice-responder call path is not wired yet.",
  },
  {
    id: "system-prompts",
    title: "System prompts",
    summary:
      "An agent's system prompt describes how it should work. Project agent files can include reusable sections with Kollab's prompt renderer.",
    howTo: [
      "Open the System Prompts settings to review the prompt used by the current agent.",
      "For a project agent, edit its system_prompt.md in that workspace's .kollab/agents/<agent-name>/ folder.",
      "Keep reusable prompt fragments in that agent's sections/ folder and include them with a supported <trender> include.",
      "Restart or reload the agent when its startup prompt changes, then verify the rendered result.",
    ],
    example:
      'Add a short “verify before reporting” section and include it with `<trender type="include" path="sections/verify.md" />`.',
    note: "Prompt sections are not Agent Skills. Skills use their own SKILL.md directory contract.",
  },
  {
    id: "phone-access",
    title: "PhoneLink",
    summary:
      "Open the same Kollab app on your phone through your existing Kollab connection. Scan a QR code, approve the phone on your computer, and continue in its browser.",
    howTo: [
      "Connect this computer to your Kollab network with /connect or Settings > Network.",
      "Open Settings > PhoneLink and choose Link a Phone. The connection service must support PhoneLink before a code is offered.",
      "Scan the QR with your phone's camera. It opens Kollab in Safari or Chrome; no WireGuard app or personal server setup is needed.",
      "Check that the pairing codes match, then approve the phone on your computer. The invitation expires after five minutes and links one phone.",
      "Bookmark Kollab to return in the same phone browser. Remove a phone in PhoneLink to withdraw its access.",
    ],
    example:
      "Start a task at your desk, then open your Kollab bookmark on your phone to check the same agents, read their work, and answer an approval request.",
    note:
      "Keep the computer awake, online, and running Kollab. PhoneLink gives a trusted phone app control; provider calls and tools run on the computer, with no credential setup on the phone. Keep the QR invitation private. Existing WireGuard access is optional under Advanced Settings.",
  },
] as const satisfies readonly LearnTopicDefinition[];

export type LearnTopicId = (typeof LEARN_TOPICS)[number]["id"];
export type LearnTopic = (typeof LEARN_TOPICS)[number] & { note?: string };

export type LearnRoute =
  | { kind: "topic"; topicId: LearnTopicId }
  | { kind: "invalid"; requestedTopic: string }
  | { kind: "empty" };

export function isLearnTopicId(value: string | null | undefined): value is LearnTopicId {
  return Boolean(value && LEARN_TOPICS.some((topic) => topic.id === value));
}

export function getLearnTopic(topicId: LearnTopicId): LearnTopic {
  // IDs are a closed union and every one has exactly one catalog entry.
  return LEARN_TOPICS.find((topic) => topic.id === topicId)!;
}

export function learnHref(topicId?: LearnTopicId): string {
  const root = typeof window !== "undefined" && window.location.pathname.startsWith("/relay/v1/phone/") ? "/relay/v1/phone" : "";
  return topicId ? `${root}/learn?topic=${encodeURIComponent(topicId)}` : `${root}/learn`;
}

export function readLearnRoute(location: { pathname: string; search: string }): LearnRoute | null {
  const pathname = location.pathname.replace(/\/+$/, "") || "/";
  if (pathname !== "/learn" && pathname !== "/relay/v1/phone/learn") return null;

  const requestedTopic = new URLSearchParams(location.search).get("topic");
  if (!requestedTopic) {
    const firstTopic = LEARN_TOPICS[0];
    return firstTopic ? { kind: "topic", topicId: firstTopic.id } : { kind: "empty" };
  }
  return isLearnTopicId(requestedTopic)
    ? { kind: "topic", topicId: requestedTopic }
    : { kind: "invalid", requestedTopic };
}

/** Navigate without reloading the chat shell; App listens for the resulting popstate. */
export function navigateToLearn(topicId?: LearnTopicId): void {
  if (typeof window === "undefined") return;
  window.history.pushState(null, "", learnHref(topicId));
  window.dispatchEvent(new PopStateEvent("popstate"));
}
