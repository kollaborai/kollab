import { ApiError } from "@/api";
import type {
  EngineApi,
  WebMessageQueueAttachment,
  WebMessageQueueEnqueueReceipt,
  WebMessageQueueIds,
} from "@/api";
import type { ComposerAttachmentLike } from "@/composer-restore";

const PLAN_PREFIX = "kollab:webui:web-queue-submission:v1";
export const WEB_QUEUE_UPDATED_EVENT = "kollab:web-message-queue-updated";
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const MAX_ITEM_IMAGE_BYTES = 20 * 1024 * 1024;
const MAX_IMAGES = 20;
const IMAGE_MIMES = new Set(["image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"]);

export type BusyWebMessageAttachment = ComposerAttachmentLike;

export type BusyWebMessageInput = Readonly<{
  sessionId: string;
  conversationUid: string;
  draftKey: string | null | undefined;
  text: string;
  attachments: readonly BusyWebMessageAttachment[];
  sendNow: boolean;
}>;

export type BusyWebMessageComposerRequest = Pick<BusyWebMessageInput,
  "draftKey" | "text" | "attachments" | "sendNow"
>;

export type BusyWebMessageResult = Readonly<{
  admitted: true;
  batchId: string;
  requestId: string;
  operationId: string;
  scopeId: string;
  incarnation: string;
  planKey: string;
  sendNowStatus: "not-requested" | "accepted" | "not-accepted" | "unknown";
}>;

/** The stored operation may already be in the daemon; its original draft must remain unchanged. */
export class QueueSubmissionReconciliationError extends Error {
  readonly requiresExactRetry = true;
}

export class QueueSubmissionRejectedError extends Error {
  readonly definitelyNotAdmitted = true;
}

export type QueueSubmissionLocks = {
  request<T>(name: string, callback: () => Promise<T>): Promise<T>;
};

export type QueuePlanStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

type SubmissionPlan = {
  version: 1;
  fingerprint: string;
  conversation_uid: string;
  scope_id: string;
  incarnation: string;
  operation_id: string;
  batch_id: string;
  request_id: string;
  attachment_operation_ids: string[];
  attachment_refs: string[];
  enqueue_attempted: boolean;
  admitted: boolean;
};

type BusyQueueApi = Pick<EngineApi,
  | "allocateWebMessageQueueIds"
  | "stageWebMessageQueueAttachment"
  | "enqueueWebMessageBatch"
  | "sendWebMessageQueueBatchNow"
>;

function storageOrThrow(storage?: QueuePlanStorage): QueuePlanStorage {
  if (storage) return storage;
  try {
    if (typeof window !== "undefined" && window.localStorage) return window.localStorage;
  } catch {
    // Storage access can throw in restricted browser contexts.
  }
  throw new Error("Could not safely retain this queue submission. The message was not sent.");
}

function planKey(input: BusyWebMessageInput): string {
  return `${PLAN_PREFIX}:${encodeURIComponent(input.sessionId)}:${encodeURIComponent(input.draftKey ?? "")}`;
}

function submissionLockName(key: string): string {
  return `${PLAN_PREFIX}:lock:${key}`;
}

function submissionLocks(explicit?: QueueSubmissionLocks): QueueSubmissionLocks {
  const locks = explicit ?? (typeof navigator !== "undefined"
    ? navigator.locks as unknown as QueueSubmissionLocks
    : undefined);
  if (!locks?.request) {
    throw new Error("This browser cannot safely coordinate queue retries across tabs. The message was not sent.");
  }
  return locks;
}

function samePlan(left: SubmissionPlan | null, right: SubmissionPlan): boolean {
  return left !== null && left.scope_id === right.scope_id &&
    left.incarnation === right.incarnation &&
    left.operation_id === right.operation_id &&
    left.batch_id === right.batch_id && left.request_id === right.request_id;
}

function discardPlan(storage: QueuePlanStorage, key: string, plan: SubmissionPlan): void {
  if (samePlan(readPlan(storage, key), plan)) storage.removeItem(key);
}

function isDefinitePayloadRejection(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if ([400, 413, 415, 422].includes(error.status)) return true;
  if (error.status !== 409 || typeof error.body !== "object" || error.body === null) return false;
  const detail = (error.body as { detail?: unknown }).detail;
  return typeof detail === "object" && detail !== null &&
    (detail as { code?: unknown }).code === "queue_capacity";
}

function definiteRejectionMessage(error: unknown, phase: "attachment" | "message"): string {
  if (error instanceof ApiError && error.status === 409 &&
      typeof error.body === "object" && error.body !== null) {
    const detail = (error.body as { detail?: unknown }).detail;
    if (typeof detail === "object" && detail !== null &&
        (detail as { code?: unknown }).code === "queue_capacity") {
      return "The queue is full; this message was not admitted. You can edit it or retry after queue space is available.";
    }
  }
  return phase === "attachment"
    ? "The daemon rejected this attachment before queue admission. Correct it and retry."
    : "The daemon rejected this message before queue admission. Correct it and retry.";
}

function readPlan(storage: QueuePlanStorage, key: string): SubmissionPlan | null {
  const raw = storage.getItem(key);
  if (raw === null) return null;
  try {
    const value = JSON.parse(raw) as Partial<SubmissionPlan>;
    if (value.version !== 1 || typeof value.fingerprint !== "string" ||
        typeof value.conversation_uid !== "string" ||
        typeof value.scope_id !== "string" || typeof value.incarnation !== "string" ||
        typeof value.operation_id !== "string" || typeof value.batch_id !== "string" ||
        typeof value.request_id !== "string" || !Array.isArray(value.attachment_operation_ids) ||
        value.attachment_operation_ids.some((id) => typeof id !== "string") ||
        (value.attachment_refs !== undefined && (!Array.isArray(value.attachment_refs) ||
          value.attachment_refs.some((ref) => typeof ref !== "string"))) ||
        (value.enqueue_attempted !== undefined && typeof value.enqueue_attempted !== "boolean") ||
        typeof value.admitted !== "boolean") {
      throw new Error("invalid stored plan");
    }
    return {
      ...value,
      attachment_refs: value.attachment_refs ?? [],
      enqueue_attempted: value.enqueue_attempted ?? false,
    } as SubmissionPlan;
  } catch {
    throw new QueueSubmissionReconciliationError("A previous queue submission cannot be verified. Keep the composer text unchanged and check the queue before retrying.");
  }
}

/** Detect an unresolved pre-admission attempt before a restored draft can be edited. */
export function hasUnresolvedBusyWebMessage(
  sessionId: string | null | undefined,
  draftKey: string | null | undefined,
  maybeStorage?: QueuePlanStorage,
): boolean {
  if (!sessionId) return false;
  if (maybeStorage === undefined && typeof window === "undefined") return false;
  const key = `${PLAN_PREFIX}:${encodeURIComponent(sessionId)}:${encodeURIComponent(draftKey ?? "")}`;
  try {
    const storage = storageOrThrow(maybeStorage);
    if (storage.getItem(key) === null) return false;
    const plan = readPlan(storage, key);
    return plan !== null && !plan.admitted && plan.enqueue_attempted;
  } catch {
    // A corrupt saved operation cannot be discarded or edited around safely.
    try {
      return storageOrThrow(maybeStorage).getItem(key) !== null;
    } catch {
      return true;
    }
  }
}

function canonicalMime(value: string): string {
  const normalized = value.toLowerCase().split(";", 1)[0]?.trim() ?? "";
  return normalized === "image/jpg" ? "image/jpeg" : normalized;
}

function dataUrlFile(attachment: BusyWebMessageAttachment): File {
  const imagePart = attachment.content?.find((part) => part.type === "image");
  const source = imagePart?.type === "image" ? imagePart.image : null;
  if (typeof source !== "string") throw new Error("The image could not be staged. Keep it in the composer and try again.");
  const match = source.match(/^data:(image\/(?:png|jpeg|jpg|webp|gif));base64,([A-Za-z0-9+/]+={0,2})$/i);
  if (!match) throw new Error("This image format cannot be queued. Keep it in the composer and try again.");
  const mime = canonicalMime(match[1]!);
  const declared = attachment.contentType ? canonicalMime(attachment.contentType) : mime;
  if (declared !== mime || !IMAGE_MIMES.has(mime)) {
    throw new Error("The image type could not be verified. Keep it in the composer and try again.");
  }
  let decoded: string;
  try {
    decoded = atob(match[2]!);
  } catch {
    throw new Error("The image data could not be read. Keep it in the composer and try again.");
  }
  const bytes = Uint8Array.from(decoded, (character) => character.charCodeAt(0));
  return new File([bytes], attachment.name || "Image", { type: mime });
}

function resolveImageFile(attachment: BusyWebMessageAttachment): File {
  if (attachment.type !== "image") {
    throw new Error("This attachment cannot be queued. Keep it in the composer and try again.");
  }
  let file: File;
  if (typeof File !== "undefined" && attachment.file instanceof File) {
    const declared = attachment.contentType ? canonicalMime(attachment.contentType) : "";
    const actual = canonicalMime(attachment.file.type);
    if (declared && actual && declared !== actual) {
      throw new Error("The image type could not be verified. Keep it in the composer and try again.");
    }
    const mime = actual || declared;
    if (!IMAGE_MIMES.has(mime)) throw new Error("This image format cannot be queued. Keep it in the composer and try again.");
    file = mime === attachment.file.type
      ? attachment.file
      : new File([attachment.file], attachment.name || attachment.file.name || "Image", { type: mime });
  } else {
    file = dataUrlFile(attachment);
  }
  if (!file.size || file.size > MAX_IMAGE_BYTES) {
    throw new Error("This image exceeds the queue size limit. Keep it in the composer and try again.");
  }
  return file;
}

async function messageFingerprint(text: string, files: readonly File[]): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error("Could not verify this queue submission safely. The message was not sent.");
  const attachments = await Promise.all(files.map(async (file) => {
    const digest = await subtle.digest("SHA-256", await file.arrayBuffer());
    const hex = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    return { name: file.name, mime: canonicalMime(file.type), size: file.size, digest: hex };
  }));
  const encoded = new TextEncoder().encode(JSON.stringify({ text, attachments }));
  const digest = await subtle.digest("SHA-256", encoded);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function validateIds(ids: WebMessageQueueIds, attachmentCount: number, conversationUid: string): SubmissionPlan {
  const operationId = ids.operation_ids[0];
  const requestId = ids.request_ids[0];
  if (!ids.scope_id || !ids.incarnation || !operationId || !ids.batch_id || !requestId ||
      ids.request_ids.length !== 1 || ids.operation_ids.length !== 1 ||
      ids.attachment_operation_ids.length !== attachmentCount) {
    throw new Error("The daemon did not allocate a complete queue submission. The message was not sent.");
  }
  return {
    version: 1,
    fingerprint: "",
    conversation_uid: conversationUid,
    scope_id: ids.scope_id,
    incarnation: ids.incarnation,
    operation_id: operationId,
    batch_id: ids.batch_id,
    request_id: requestId,
    attachment_operation_ids: [...ids.attachment_operation_ids],
    attachment_refs: [],
    enqueue_attempted: false,
    admitted: false,
  };
}

function validateReceipt(receipt: WebMessageQueueEnqueueReceipt, plan: SubmissionPlan): boolean {
  return receipt.scope_id === plan.scope_id && receipt.incarnation === plan.incarnation &&
    receipt.operation_id === plan.operation_id && receipt.status === "queued" &&
    receipt.batch_id === plan.batch_id && receipt.request_ids.length === 1 &&
    receipt.request_ids[0] === plan.request_id;
}

export async function submitBusyWebMessage(
  api: BusyQueueApi,
  input: BusyWebMessageInput,
  maybeStorage?: QueuePlanStorage,
  maybeLocks?: QueueSubmissionLocks,
): Promise<BusyWebMessageResult> {
  if (!input.sessionId || !input.conversationUid || (!input.text.trim() && input.attachments.length === 0)) {
    throw new Error("This message is not ready to queue.");
  }
  if (input.attachments.length > MAX_IMAGES) {
    throw new Error("Too many images to queue at once. Keep them in the composer and try again.");
  }
  const files: File[] = [];
  let totalImageBytes = 0;
  for (const attachment of input.attachments) {
    const file = resolveImageFile(attachment);
    totalImageBytes += file.size;
    if (totalImageBytes > MAX_ITEM_IMAGE_BYTES) {
      throw new Error("These images exceed the queue's 20 MiB per-message limit. Remove an image and retry.");
    }
    files.push(file);
  }
  const payloadBytes = new TextEncoder().encode(JSON.stringify({ text: input.text })).byteLength;
  if (payloadBytes > 1024 * 1024) {
    throw new Error("This message exceeds the queue's 1 MiB limit. Shorten it and retry.");
  }
  const fingerprint = await messageFingerprint(input.text, files);
  const storage = storageOrThrow(maybeStorage);
  const key = planKey(input);
  const locks = submissionLocks(maybeLocks);
  let entered = false;
  try {
    return await locks.request(submissionLockName(key), async () => {
      entered = true;
      return submitBusyWebMessageLocked(api, input, storage, key, files, fingerprint);
    });
  } catch (error) {
    if (!entered) {
      throw new Error("Could not obtain the browser's cross-tab queue lock. The message was not sent; keep it in the composer and retry.");
    }
    throw error;
  }
}

async function submitBusyWebMessageLocked(
  api: BusyQueueApi,
  input: BusyWebMessageInput,
  storage: QueuePlanStorage,
  key: string,
  files: readonly File[],
  fingerprint: string,
): Promise<BusyWebMessageResult> {
  let plan = readPlan(storage, key);
  if (plan && !plan.admitted &&
      (plan.fingerprint !== fingerprint || plan.conversation_uid !== input.conversationUid)) {
    if (plan.enqueue_attempted) {
      throw new QueueSubmissionReconciliationError("A previous queue submission is unresolved. Keep its message unchanged and check the queue before retrying.");
    }
    // Only staged blobs exist, so no queue item can have been admitted.
    discardPlan(storage, key, plan);
    plan = null;
  }
  if (plan?.admitted &&
      (plan.fingerprint !== fingerprint || plan.conversation_uid !== input.conversationUid)) plan = null;

  if (!plan) {
    const ids = await api.allocateWebMessageQueueIds(
      input.sessionId,
      input.conversationUid,
      1,
      1,
      files.length,
    );
    plan = { ...validateIds(ids, files.length, input.conversationUid), fingerprint };
    storage.setItem(key, JSON.stringify(plan));
  }

  if (plan.fingerprint !== fingerprint || plan.conversation_uid !== input.conversationUid ||
      plan.attachment_operation_ids.length !== files.length) {
    if (plan.enqueue_attempted) {
      throw new QueueSubmissionReconciliationError("A previous queue submission is unresolved. Keep its message unchanged and check the queue before retrying.");
    }
    discardPlan(storage, key, plan);
    const ids = await api.allocateWebMessageQueueIds(
      input.sessionId,
      input.conversationUid,
      1,
      1,
      files.length,
    );
    plan = { ...validateIds(ids, files.length, input.conversationUid), fingerprint };
    storage.setItem(key, JSON.stringify(plan));
  }

  if (!plan.admitted) {
    if (plan.enqueue_attempted) {
      if (plan.attachment_refs.length !== files.length || plan.attachment_refs.some((ref) => !ref)) {
        throw new QueueSubmissionReconciliationError("A previous queue submission has incomplete attachment receipts. Keep the composer content unchanged and check the queue before retrying.");
      }
    } else {
      const staged: WebMessageQueueAttachment[] = [];
      try {
        for (let index = 0; index < files.length; index += 1) {
          const operationId = plan.attachment_operation_ids[index];
          if (!operationId) throw new Error("The image staging plan is incomplete.");
          const attachment = await api.stageWebMessageQueueAttachment(
            input.sessionId,
            input.conversationUid,
            operationId,
            files[index]!,
          );
          if (!attachment.ref || canonicalMime(attachment.mime) !== canonicalMime(files[index]!.type) ||
              attachment.size !== files[index]!.size) {
            throw new Error("The daemon did not verify the staged image.");
          }
          staged.push(attachment);
          plan = { ...plan, attachment_refs: staged.map((entry) => entry.ref) };
          storage.setItem(key, JSON.stringify(plan));
        }
      } catch (error) {
        // No enqueue call has happened. Any staged refs are unowned and expire
        // under the daemon's staging TTL, so this draft can be corrected safely.
        discardPlan(storage, key, plan);
        throw new QueueSubmissionRejectedError(isDefinitePayloadRejection(error)
          ? definiteRejectionMessage(error, "attachment")
          : "Could not stage the attachment, so it was not queued. Keep it in the composer and retry.");
      }
    }

    plan = { ...plan, enqueue_attempted: true };
    try {
      storage.setItem(key, JSON.stringify(plan));
    } catch {
      discardPlan(storage, key, { ...plan, enqueue_attempted: false });
      throw new Error("Could not safely record the queue attempt. The message was not submitted; retry it.");
    }

    let receipt: WebMessageQueueEnqueueReceipt;
    try {
      receipt = await api.enqueueWebMessageBatch(input.sessionId, {
        conversation_uid: input.conversationUid,
        operation_id: plan.operation_id,
        batch_id: plan.batch_id,
        items: [{
          request_id: plan.request_id,
          text: input.text,
          attachment_refs: plan.attachment_refs,
        }],
      });
    } catch (error) {
      if (isDefinitePayloadRejection(error)) {
        discardPlan(storage, key, plan);
        throw new QueueSubmissionRejectedError(definiteRejectionMessage(error, "message"));
      }
      throw new QueueSubmissionReconciliationError("Could not confirm queue admission. Keep the composer message unchanged and retry it to reconcile the same submission.");
    }
    if (!validateReceipt(receipt, plan)) {
      throw new QueueSubmissionReconciliationError("Could not confirm queue admission. Keep the composer message unchanged and check the queue before retrying.");
    }
    plan = { ...plan, admitted: true };
    try {
      storage.setItem(key, JSON.stringify(plan));
    } catch {
      throw new QueueSubmissionReconciliationError("The message was admitted, but its local receipt could not be retained. Keep the composer content and check the queue before retrying.");
    }
  }

  let sendNowStatus: BusyWebMessageResult["sendNowStatus"] = "not-requested";
  if (input.sendNow) {
    try {
      const result = await api.sendWebMessageQueueBatchNow(
        input.sessionId,
        input.conversationUid,
        plan.batch_id,
      );
      sendNowStatus = result.accepted === true ? "accepted" : "not-accepted";
    } catch {
      sendNowStatus = "unknown";
    }
  }

  if (typeof window !== "undefined") window.dispatchEvent(new Event(WEB_QUEUE_UPDATED_EVENT));

  return {
    admitted: true,
    batchId: plan.batch_id,
    requestId: plan.request_id,
    operationId: plan.operation_id,
    scopeId: plan.scope_id,
    incarnation: plan.incarnation,
    planKey: key,
    sendNowStatus,
  };
}

/** Call only after the exact unchanged composer draft has been cleared. */
export function acknowledgeBusyWebMessage(
  result: BusyWebMessageResult,
  maybeStorage?: QueuePlanStorage,
  maybeLocks?: QueueSubmissionLocks,
): Promise<boolean> {
  const acknowledge = async () => {
    try {
      const storage = storageOrThrow(maybeStorage);
      const current = readPlan(storage, result.planKey);
      if (!current) return true;
      if (current.scope_id === result.scopeId && current.incarnation === result.incarnation &&
          current.operation_id === result.operationId && current.batch_id === result.batchId &&
          current.request_id === result.requestId) {
        storage.removeItem(result.planKey);
      }
      // A newer submission owns the shared draft key; this older response may
      // acknowledge successfully but must never erase the newer retry plan.
      return true;
    } catch {
      return false;
    }
  };
  try {
    return submissionLocks(maybeLocks).request(submissionLockName(result.planKey), acknowledge);
  } catch {
    // Keep the plan as an idempotency guard if cross-tab arbitration is unavailable.
    return Promise.resolve(false);
  }
}
