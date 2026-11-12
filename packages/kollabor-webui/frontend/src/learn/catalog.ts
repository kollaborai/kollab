import networks from "./topics/networks.json" with { type: "json" };
import devices from "./topics/devices.json" with { type: "json" };
import workspaces from "./topics/workspaces.json" with { type: "json" };
import agents from "./topics/agents.json" with { type: "json" };
import profiles from "./topics/profiles.json" with { type: "json" };
import contextCompaction from "./topics/context-compaction.json" with { type: "json" };
import koordinator from "./topics/koordinator.json" with { type: "json" };
import hubMessaging from "./topics/hub-messaging.json" with { type: "json" };
import voice from "./topics/voice.json" with { type: "json" };
import systemPrompts from "./topics/system-prompts.json" with { type: "json" };
import phoneAccess from "./topics/phone-access.json" with { type: "json" };
import type { LearnTopicDefinition } from "./model";

/** Stable IDs are URL contracts; only display the networking lesson as Connect. */
const LEARN_TOPIC_IDS = [
  "networks",
  "devices",
  "workspaces",
  "agents",
  "profiles",
  "context-compaction",
  "koordinator",
  "hub-messaging",
  "voice",
  "system-prompts",
  "phone-access",
] as const;

export type LearnTopicId = (typeof LEARN_TOPIC_IDS)[number];
export type LearnTopic = LearnTopicDefinition & { id: LearnTopicId };
export type { LearnCommand, LearnMedia, LearnStep, LearnTopicDefinition, LearnWorkflow } from "./model";

const topicsById: Readonly<Record<LearnTopicId, LearnTopic>> = {
  networks: networks as LearnTopic,
  devices: devices as LearnTopic,
  workspaces: workspaces as LearnTopic,
  agents: agents as LearnTopic,
  profiles: profiles as LearnTopic,
  "context-compaction": contextCompaction as LearnTopic,
  koordinator: koordinator as LearnTopic,
  "hub-messaging": hubMessaging as LearnTopic,
  voice: voice as LearnTopic,
  "system-prompts": systemPrompts as LearnTopic,
  "phone-access": phoneAccess as LearnTopic,
};

export const LEARN_TOPICS: readonly LearnTopic[] = LEARN_TOPIC_IDS.map((id) => topicsById[id]);

export type LearnRoute =
  | { kind: "topic"; topicId: LearnTopicId }
  | { kind: "invalid"; requestedTopic: string }
  | { kind: "empty" };

export function isLearnTopicId(value: string | null | undefined): value is LearnTopicId {
  return Boolean(value && Object.hasOwn(topicsById, value));
}

export function getLearnTopic(topicId: LearnTopicId): LearnTopic {
  return topicsById[topicId];
}

export function learnHref(topicId?: LearnTopicId): string {
  const root = typeof window !== "undefined" && window.location.pathname.startsWith("/relay/v1/phone/") ? "/relay/v1/phone" : "";
  return topicId ? `${root}/learn?topic=${encodeURIComponent(topicId)}` : `${root}/learn`;
}

export function readLearnRoute(location: { pathname: string; search: string }): LearnRoute | null {
  const pathname = location.pathname.replace(/\/+$/, "") || "/";
  if (pathname !== "/learn" && pathname !== "/relay/v1/phone/learn") return null;

  const requestedTopic = new URLSearchParams(location.search).get("topic");
  if (!requestedTopic) return { kind: "topic", topicId: LEARN_TOPIC_IDS[0] };
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
