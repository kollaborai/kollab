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
  queuedDraft: { fingerprint: string; error: unknown } | null;
  ambiguousDraft: { fingerprint: string; error: unknown } | null;
  observedFingerprint: string | null;
  revision: number;
};

export type BusyComposerSubmitIssue = {
  kind: "prepare" | "ambiguous" | "reset" | "already-queued";
  error: unknown;
};

export function busyComposerSubmitErrorMessage(
  issue: BusyComposerSubmitIssue,
): string {
  switch (issue.kind) {
    case "prepare":
      return "Could not prepare the image. Your draft is still here; try sending again.";
    case "ambiguous":
      return (
        "Could not confirm whether this message entered the send queue. " +
        "Your draft is still here. Check the conversation before allowing " +
        "another send; it may create a duplicate."
      );
    case "reset":
      return "Message queued, but the draft could not be cleared. Clear or edit it before submitting again.";
    case "already-queued":
      return "This draft was already queued. Clear or edit it before submitting again.";
  }
}

export function createBusyComposerSubmitTracker(): BusyComposerSubmitTracker {
  return {
    last: null,
    inFlight: new Set(),
    queuedDraft: null,
    ambiguousDraft: null,
    observedFingerprint: null,
    revision: 0,
  };
}

export function clearAmbiguousBusyComposerGuard(
  tracker: BusyComposerSubmitTracker,
): boolean {
  if (!tracker.ambiguousDraft) return false;
  tracker.ambiguousDraft = null;
  tracker.last = null;
  return true;
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
    if (tracker.queuedDraft) {
      tracker.queuedDraft = null;
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
  sendCommand: (command: BusyAddMessageCommand) => void;
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
    const ambiguousDraft = options.tracker.ambiguousDraft;
    if (ambiguousDraft) {
      event.preventDefault();
      options.onError({ kind: "ambiguous", error: ambiguousDraft.error });
      return;
    }
    if (draftChanged) options.onError(null);

    if (!options.isRunning) {
      const fingerprint = busyComposerFingerprint(state);
      const alreadyQueued = options.tracker.queuedDraft;
      if (alreadyQueued?.fingerprint === fingerprint) {
        event.preventDefault();
        options.onError({ kind: "already-queued", error: alreadyQueued.error });
        return;
      }
      if (alreadyQueued) {
        options.tracker.queuedDraft = null;
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
  const alreadyQueued = tracker.queuedDraft;
  if (alreadyQueued?.fingerprint === fingerprint) {
    event.preventDefault();
    onError({ kind: "already-queued", error: alreadyQueued.error });
    return;
  }
  if (alreadyQueued) {
    tracker.queuedDraft = null;
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
      // The callback acknowledges only a synchronous queue handoff. The runtime
      // reports any later HTTP failures separately.
      sendCommand({
        type: "add-message",
        message: { role: "user", parts },
        parentId: null,
        sourceId: null,
      });
    } catch (error) {
      // The queue may contain the command if scheduling failed after enqueue.
      tracker.ambiguousDraft = { fingerprint, error };
      onError({ kind: "ambiguous", error });
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
      tracker.queuedDraft = { fingerprint, error };
      onError({ kind: "reset", error });
    }
  } finally {
    tracker.inFlight.delete(fingerprint);
  }
}
