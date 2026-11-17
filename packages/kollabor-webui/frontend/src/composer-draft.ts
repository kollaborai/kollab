export type ComposerDraftStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export type ComposerDraftSubmission = Readonly<{
  key: string;
  text: string;
  revision: number;
}>;

import type { StoredComposerAttachment } from "@/composer-restore";

const DRAFT_PREFIX = "kollab:webui:composer-draft:v1";

export type ComposerAttachmentStorage = Readonly<{
  get(key: string): readonly StoredComposerAttachment[] | undefined | Promise<readonly StoredComposerAttachment[] | undefined>;
  set(key: string, value: readonly StoredComposerAttachment[]): unknown | Promise<unknown>;
  delete(key: string): unknown | Promise<unknown>;
}>;

const ATTACHMENT_DATABASE = "kollab:webui:composer-attachments:v1";
const ATTACHMENT_STORE = "drafts";

/** Keep same-tab restores synchronous while IndexedDB supplies reload durability. */
const attachmentsByKey = new Map<string, readonly StoredComposerAttachment[]>();
let attachmentDatabase: Promise<IDBDatabase | null> | null = null;

function openAttachmentDatabase(): Promise<IDBDatabase | null> {
  if (attachmentDatabase) return attachmentDatabase;
  attachmentDatabase = new Promise((resolve) => {
    if (typeof indexedDB === "undefined") {
      resolve(null);
      return;
    }
    try {
      const request = indexedDB.open(ATTACHMENT_DATABASE, 1);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains(ATTACHMENT_STORE)) {
          database.createObjectStore(ATTACHMENT_STORE);
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return attachmentDatabase;
}

const indexedDbAttachmentStorage: ComposerAttachmentStorage = {
  async get(key) {
    const database = await openAttachmentDatabase();
    if (!database) return undefined;
    return new Promise((resolve) => {
      try {
        const request = database
          .transaction(ATTACHMENT_STORE, "readonly")
          .objectStore(ATTACHMENT_STORE)
          .get(key);
        request.onsuccess = () => resolve(request.result ?? undefined);
        request.onerror = () => resolve(undefined);
      } catch {
        resolve(undefined);
      }
    });
  },
  async set(key, value) {
    const database = await openAttachmentDatabase();
    if (!database) return;
    await new Promise<void>((resolve) => {
      try {
        const transaction = database.transaction(ATTACHMENT_STORE, "readwrite");
        transaction.objectStore(ATTACHMENT_STORE).put(value, key);
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => resolve();
        transaction.onabort = () => resolve();
      } catch {
        resolve();
      }
    });
  },
  async delete(key) {
    const database = await openAttachmentDatabase();
    if (!database) return;
    await new Promise<void>((resolve) => {
      try {
        const transaction = database.transaction(ATTACHMENT_STORE, "readwrite");
        transaction.objectStore(ATTACHMENT_STORE).delete(key);
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => resolve();
        transaction.onabort = () => resolve();
      } catch {
        resolve();
      }
    });
  },
};

export function saveComposerAttachments(
  key: string | null | undefined,
  attachments: readonly StoredComposerAttachment[],
  storage?: ComposerAttachmentStorage,
): void | Promise<void> {
  if (!key) return;
  if (attachments.length === 0) {
    attachmentsByKey.delete(key);
    if (storage) return Promise.resolve(storage.delete(key)).then(() => undefined);
    void indexedDbAttachmentStorage.delete(key);
    return;
  }
  attachmentsByKey.set(key, attachments);
  if (storage) return Promise.resolve(storage.set(key, attachments)).then(() => undefined);
  void indexedDbAttachmentStorage.set(key, attachments);
}

export function loadComposerAttachments(
  key: string | null | undefined,
): StoredComposerAttachment[];
export function loadComposerAttachments(
  key: string | null | undefined,
  storage: ComposerAttachmentStorage,
): Promise<readonly StoredComposerAttachment[]>;
export function loadComposerAttachments(
  key: string | null | undefined,
  storage?: ComposerAttachmentStorage,
): StoredComposerAttachment[] | Promise<readonly StoredComposerAttachment[]> {
  if (!key) return [];
  const retained = attachmentsByKey.get(key);
  if (retained) return [...retained];
  if (!storage) return [];
  return Promise.resolve(storage.get(key)).then((attachments) => {
    if (!attachments || attachments.length === 0) return [];
    attachmentsByKey.set(key, attachments);
    return attachments;
  }).catch(() => []);
}

export async function loadPersistedComposerAttachments(
  key: string | null | undefined,
  storage?: ComposerAttachmentStorage,
): Promise<readonly StoredComposerAttachment[]> {
  if (!key) return [];
  const retained = attachmentsByKey.get(key);
  if (retained) return retained;
  const target = storage ?? indexedDbAttachmentStorage;
  return Promise.resolve(target.get(key)).then((attachments) => {
    if (!attachments || attachments.length === 0) return [];
    attachmentsByKey.set(key, attachments);
    return attachments;
  }).catch(() => []);
}
const revisions = new Map<string, number>();
const pendingSubmissions = new Map<string, ComposerDraftSubmission[]>();
// Keep failed requests' tickets until a newer edit or a successful response.
const inFlightSubmissions = new Map<string, ComposerDraftSubmission[]>();

function browserStorage(): ComposerDraftStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function resolveStorage(storage?: ComposerDraftStorage | null): ComposerDraftStorage | null {
  return storage === undefined ? browserStorage() : storage;
}

/** A draft belongs to one agent conversation, never to a shared browser-wide key. */
export function composerDraftStorageKey(
  agentIdentity?: string | null,
  conversationId?: string | null,
): string | null {
  const agent = agentIdentity?.trim();
  const conversation = conversationId?.trim();
  if (!agent || !conversation) return null;
  return `${DRAFT_PREFIX}:${encodeURIComponent(agent)}:${encodeURIComponent(conversation)}`;
}

export function loadComposerDraft(
  key: string | null | undefined,
  storage?: ComposerDraftStorage | null,
): string | null {
  if (!key) return null;
  try {
    return resolveStorage(storage)?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

/** Persist every composer change; a newer edit must invalidate an older send ticket. */
export function saveComposerDraft(
  key: string | null | undefined,
  text: string,
  storage?: ComposerDraftStorage | null,
): void {
  if (!key) return;
  if (text.length > 0) inFlightSubmissions.delete(key);
  if (text.length === 0) {
    // The transport clears the visible composer before it knows whether send succeeded.
    const submittedDraft = [
      ...(pendingSubmissions.get(key) ?? []),
      ...(inFlightSubmissions.get(key) ?? []),
    ].some((submission) => loadComposerDraft(key, storage) === submission.text);
    if (submittedDraft) return;
  }
  revisions.set(key, (revisions.get(key) ?? 0) + 1);
  try {
    const target = resolveStorage(storage);
    if (!target) return;
    if (text.length > 0) target.setItem(key, text);
    else target.removeItem(key);
  } catch {
    // Storage can be disabled, full, or unavailable in a private browsing mode.
  }
}

/**
 * Snapshot the exact text/revision that is about to be sent. The send request
 * may clear storage only if no subsequent composer edit has replaced it.
 */
export function queueComposerDraftSubmission(
  key: string | null | undefined,
  text: string,
  storage?: ComposerDraftStorage | null,
): void {
  if (!key || text.length === 0) return;
  // The user can submit before the debounced/render effect has written the
  // latest keystroke. Make the submitted value durable before transport starts.
  if (loadComposerDraft(key, storage) !== text) saveComposerDraft(key, text, storage);
  const submission: ComposerDraftSubmission = {
    key,
    text,
    revision: revisions.get(key) ?? 0,
  };
  const queue = (pendingSubmissions.get(key) ?? []).filter((pending) =>
    pending.revision === submission.revision && pending.text !== submission.text);
  queue.push(submission);
  pendingSubmissions.set(key, queue);
}

export function takeComposerDraftSubmission(
  key: string | null | undefined,
): ComposerDraftSubmission | null {
  if (!key) return null;
  const queue = pendingSubmissions.get(key);
  const submission = queue?.shift() ?? null;
  if (queue?.length === 0) pendingSubmissions.delete(key);
  if (submission) {
    inFlightSubmissions.set(key, [
      ...(inFlightSubmissions.get(key) ?? []),
      submission,
    ]);
  }
  return submission;
}

/** Clear the stored draft only after its matching transport response succeeds. */
export function completeComposerDraftSubmission(
  submission: ComposerDraftSubmission | null | undefined,
  storage?: ComposerDraftStorage | null,
): boolean {
  if (!submission) return false;
  if ((revisions.get(submission.key) ?? 0) !== submission.revision) return false;
  if (loadComposerDraft(submission.key, storage) !== submission.text) return false;
  try {
    const target = resolveStorage(storage);
    if (!target) return false;
    target.removeItem(submission.key);
    revisions.set(submission.key, submission.revision + 1);
    const inFlight = inFlightSubmissions.get(submission.key);
    const remaining = inFlight?.filter(
      (candidate) => candidate !== submission,
    ) ?? [];
    if (remaining.length > 0) inFlightSubmissions.set(submission.key, remaining);
    else inFlightSubmissions.delete(submission.key);
    return true;
  } catch {
    return false;
  }
}
