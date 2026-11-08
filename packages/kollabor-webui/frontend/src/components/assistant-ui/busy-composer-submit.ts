import type {
  Attachment,
  PendingAttachment,
  ThreadUserMessagePart,
  CompleteAttachment,
} from "@assistant-ui/react";

export type BusyComposerState = {
  text: string;
  attachments: readonly Attachment[];
};

type BusyMessagePart =
  | { type: "text"; text: string }
  | { type: "image"; image: string };

type BusyAddMessageCommand = {
  type: "add-message";
  message: { role: "user"; parts: BusyMessagePart[] };
  parentId: null;
  sourceId: null;
};

export type BusyComposerSubmitTracker = {
  last: { fingerprint: string; at: number } | null;
  inFlight: Set<string>;
  sentDraft: { fingerprint: string; error: unknown } | null;
  observedFingerprint: string | null;
  revision: number;
};

export type BusyComposerSubmitIssue = {
  kind: "prepare" | "transport" | "reset" | "already-sent";
  error: unknown;
};

export function busyComposerSubmitErrorMessage(
  issue: BusyComposerSubmitIssue,
): string {
  switch (issue.kind) {
    case "prepare":
      return "Could not prepare the image. Your draft is still here; try sending again.";
    case "transport":
      return "Could not send this message. Your draft is still here; try sending again.";
    case "reset":
      return "Message sent, but the draft could not be cleared. Clear or edit it before submitting again.";
    case "already-sent":
      return "This draft was already sent. Clear or edit it before submitting again.";
  }
}

export function createBusyComposerSubmitTracker(): BusyComposerSubmitTracker {
  return {
    last: null,
    inFlight: new Set(),
    sentDraft: null,
    observedFingerprint: null,
    revision: 0,
  };
}

export function busyComposerFingerprint(state: BusyComposerState): string {
  return `${state.text}\u0000${state.attachments
    .map((attachment) => attachment.id)
    .join(",")}`;
}

export function observeBusyComposerDraft(
  tracker: BusyComposerSubmitTracker,
  state: BusyComposerState,
): boolean {
  const fingerprint = busyComposerFingerprint(state);
  const changed =
    tracker.observedFingerprint !== null &&
    tracker.observedFingerprint !== fingerprint;
  tracker.observedFingerprint = fingerprint;

  if (changed) {
    tracker.revision += 1;
    if (tracker.sentDraft) {
      tracker.sentDraft = null;
      tracker.last = null;
      return true;
    }
  }
  return false;
}

export type BusyComposerSubmitOptions = {
  state: BusyComposerState;
  event: { preventDefault: () => void; defaultPrevented?: boolean };
  tracker: BusyComposerSubmitTracker;
  finalizeImageAttachment: (
    attachment: PendingAttachment,
  ) => Promise<CompleteAttachment>;
  sendCommand: (command: BusyAddMessageCommand) => void | Promise<void>;
  getCurrentState: () => BusyComposerState;
  resetComposer: () => void | Promise<void>;
  onError: (issue: BusyComposerSubmitIssue | null) => void;
  now?: () => number;
};

export type BusyComposerSubmitHandlerOptions = Omit<
  BusyComposerSubmitOptions,
  "state" | "event" | "getCurrentState"
> & {
  isRunning: boolean;
  getState: () => BusyComposerState;
};

export function createBusyComposerSubmitHandler(
  options: BusyComposerSubmitHandlerOptions,
): (event: { preventDefault: () => void; defaultPrevented?: boolean }) =>
  | Promise<void>
  | void {
  return (event) => {
    if (event.defaultPrevented) return;
    const state = options.getState();
    const draftChanged = observeBusyComposerDraft(options.tracker, state);
    if (draftChanged) options.onError(null);

    if (!options.isRunning) {
      const fingerprint = busyComposerFingerprint(state);
      const alreadySent = options.tracker.sentDraft;
      if (alreadySent?.fingerprint === fingerprint) {
        event.preventDefault();
        options.onError({ kind: "already-sent", error: alreadySent.error });
        return;
      }
      if (alreadySent) {
        options.tracker.sentDraft = null;
        options.tracker.last = null;
      }
      if (!draftChanged) options.onError(null);
      return;
    }

    return submitBusyComposerMessage({
      state,
      event,
      tracker: options.tracker,
      finalizeImageAttachment: options.finalizeImageAttachment,
      sendCommand: options.sendCommand,
      getCurrentState: options.getState,
      resetComposer: options.resetComposer,
      onError: options.onError,
      now: options.now,
    });
  };
}

async function submitBusyComposerMessage(
  options: BusyComposerSubmitOptions,
): Promise<void> {
  const {
    state,
    event,
    tracker,
    finalizeImageAttachment,
    sendCommand,
    getCurrentState,
    resetComposer,
    onError,
  } = options;
  if (event.defaultPrevented) return;
  if (!state.text.trim() && state.attachments.length === 0) return;

  const fingerprint = busyComposerFingerprint(state);
  const draftRevision = tracker.revision;
  const alreadySent = tracker.sentDraft;
  if (alreadySent?.fingerprint === fingerprint) {
    event.preventDefault();
    onError({ kind: "already-sent", error: alreadySent.error });
    return;
  }
  if (alreadySent) {
    tracker.sentDraft = null;
    tracker.last = null;
  }

  const now = (options.now ?? Date.now)();
  const previous = tracker.last;
  if (
    tracker.inFlight.has(fingerprint) ||
    (previous?.fingerprint === fingerprint && now - previous.at < 450)
  ) {
    event.preventDefault();
    return;
  }

  event.preventDefault();
  tracker.inFlight.add(fingerprint);
  tracker.last = { fingerprint, at: now };

  try {
    onError(null);
    const parts: BusyMessagePart[] = [];
    if (state.text.trim()) parts.push({ type: "text", text: state.text });

    try {
      for (const attachment of state.attachments) {
        let content: readonly ThreadUserMessagePart[] = attachment.content ?? [];
        if (
          attachment.type === "image" &&
          attachment.status.type !== "complete" &&
          !content.some((part) => part.type === "image")
        ) {
          const complete = await finalizeImageAttachment(
            attachment as PendingAttachment,
          );
          content = complete.content;
          if (!content.some((part) => part.type === "image")) {
            throw new Error("The image adapter returned no image content.");
          }
        }

        for (const part of content) {
          if (part.type === "text") parts.push({ type: "text", text: part.text });
          if (part.type === "image") parts.push({ type: "image", image: part.image });
        }
      }
    } catch (error) {
      tracker.last = null;
      onError({ kind: "prepare", error });
      return;
    }

    if (parts.length === 0) {
      tracker.last = null;
      return;
    }

    try {
      await sendCommand({
        type: "add-message",
        message: { role: "user", parts },
        parentId: null,
        sourceId: null,
      });
    } catch (error) {
      tracker.last = null;
      onError({ kind: "transport", error });
      return;
    }

    try {
      if (
        tracker.revision !== draftRevision ||
        busyComposerFingerprint(getCurrentState()) !== fingerprint
      ) {
        return;
      }
      await resetComposer();
    } catch (error) {
      tracker.sentDraft = { fingerprint, error };
      onError({ kind: "reset", error });
    }
  } finally {
    tracker.inFlight.delete(fingerprint);
  }
}
