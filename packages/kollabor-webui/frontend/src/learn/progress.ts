import { isLearnTopicId, LEARN_TOPICS, type LearnTopicId } from "./catalog.ts";

export const LEARN_PROGRESS_STORAGE_KEY = "kollab.learn.progress.v1";

export type LearnStorage = Pick<Storage, "getItem" | "setItem">;
export type LearnProgressRetryOperation = "read" | "write" | null;

export type LearnProgressState = {
  completed: LearnTopicId[];
  warning: string | null;
  canRetry: boolean;
  canPersist: boolean;
  retryOperation: LearnProgressRetryOperation;
};

const STORAGE_READ_WARNING =
  "Saved progress could not be read. Enable site storage or continue without prior progress; existing data will not be overwritten until it can be read.";
const STORAGE_WRITE_WARNING =
  "Lesson progress could not be saved. Enable site storage or keep learning without persistence.";
const CORRUPT_WARNING =
  "Saved progress was unreadable; existing data was left untouched. Clear the kollab.learn.progress.v1 browser entry and reload to start fresh.";
const UNKNOWN_TOPIC_WARNING =
  "Some saved topics are no longer available; the stored entry was left untouched. Clear the kollab.learn.progress.v1 browser entry to reset it.";

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
    return {
      completed: [],
      warning: STORAGE_READ_WARNING,
      canRetry: true,
      canPersist: false,
      retryOperation: "read",
    };
  }
  if (!raw) {
    return { completed: [], warning: null, canRetry: false, canPersist: true, retryOperation: null };
  }

  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return {
      completed: [],
      warning: CORRUPT_WARNING,
      canRetry: false,
      canPersist: false,
      retryOperation: null,
    };
  }
  if (!Array.isArray(value)) {
    return {
      completed: [],
      warning: CORRUPT_WARNING,
      canRetry: false,
      canPersist: false,
      retryOperation: null,
    };
  }

  const completed = normalizeLearnProgress(value);
  const hasUnknownTopic = value.some((id) => !isLearnTopicId(id));
  return {
    completed,
    warning: hasUnknownTopic ? UNKNOWN_TOPIC_WARNING : null,
    canRetry: false,
    canPersist: !hasUnknownTopic,
    retryOperation: null,
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
    return STORAGE_WRITE_WARNING;
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

export function applyToggleLearnProgress(
  state: LearnProgressState,
  topicId: LearnTopicId,
  storage?: LearnStorage,
): LearnProgressState {
  if (!state.canPersist) {
    return state;
  }
  const completedTopics = toggleLearnProgress(state.completed, topicId);
  const warning = writeLearnProgress(completedTopics, storage);
  return {
    completed: completedTopics,
    warning,
    canRetry: Boolean(warning),
    canPersist: true,
    retryOperation: warning ? "write" : null,
  };
}
