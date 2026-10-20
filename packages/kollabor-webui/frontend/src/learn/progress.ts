import { isLearnTopicId, LEARN_TOPICS, type LearnTopicId } from "./catalog.ts";

export const LEARN_PROGRESS_STORAGE_KEY = "kollab.learn.progress.v1";

export type LearnStorage = Pick<Storage, "getItem" | "setItem">;

export type LearnProgressState = {
  completed: LearnTopicId[];
  warning: string | null;
  canRetry: boolean;
};

const STORAGE_WARNING =
  "Lesson progress cannot be read or saved in this browser. Enable site storage to persist completion; you can keep learning without it.";
const CORRUPT_WARNING =
  "Saved lesson progress was unreadable and has been ignored. Completing a topic will save a fresh progress list.";
const UNKNOWN_TOPIC_WARNING =
  "Some saved topics are no longer available. Remaining progress was kept; continue learning.";

function normalizeLearnProgress(value: unknown): LearnTopicId[] {
  if (!Array.isArray(value)) return [];
  const valid = new Set(value.filter((id): id is LearnTopicId => isLearnTopicId(id)));
  return LEARN_TOPICS.filter((topic) => valid.has(topic.id)).map((topic) => topic.id);
}

export function parseLearnProgress(raw: string | null): LearnTopicId[] {
  if (!raw) return [];
  try {
    return normalizeLearnProgress(JSON.parse(raw));
  } catch {
    return [];
  }
}

function getStorage(storage?: LearnStorage): LearnStorage {
  if (storage) return storage;
  if (typeof window === "undefined") throw new Error("Browser storage is unavailable.");
  return window.localStorage;
}

export function readLearnProgress(storage?: LearnStorage): LearnProgressState {
  let raw: string | null;
  try {
    raw = getStorage(storage).getItem(LEARN_PROGRESS_STORAGE_KEY);
  } catch {
    return { completed: [], warning: STORAGE_WARNING, canRetry: true };
  }
  if (!raw) return { completed: [], warning: null, canRetry: false };

  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return { completed: [], warning: CORRUPT_WARNING, canRetry: false };
  }
  if (!Array.isArray(value)) return { completed: [], warning: CORRUPT_WARNING, canRetry: false };

  const completed = normalizeLearnProgress(value);
  const hasUnknownTopic = value.some((id) => !isLearnTopicId(id));
  return {
    completed,
    warning: hasUnknownTopic ? UNKNOWN_TOPIC_WARNING : null,
    canRetry: false,
  };
}

export function writeLearnProgress(
  completed: readonly LearnTopicId[],
  storage?: LearnStorage,
): string | null {
  try {
    const stableOrder = normalizeLearnProgress(completed);
    getStorage(storage).setItem(LEARN_PROGRESS_STORAGE_KEY, JSON.stringify(stableOrder));
    return null;
  } catch {
    return STORAGE_WARNING;
  }
}

export function toggleLearnProgress(
  completed: readonly LearnTopicId[],
  topicId: LearnTopicId,
): LearnTopicId[] {
  const next = new Set(completed);
  if (next.has(topicId)) next.delete(topicId);
  else next.add(topicId);
  return LEARN_TOPICS.filter((topic) => next.has(topic.id)).map((topic) => topic.id);
}
