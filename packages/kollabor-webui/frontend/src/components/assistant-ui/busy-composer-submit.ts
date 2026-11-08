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
};

export function createBusyComposerSubmitTracker(): BusyComposerSubmitTracker {
  return { last: null, inFlight: new Set() };
}

export function busyComposerFingerprint(state: BusyComposerState): string {
  return `${state.text}\u0000${state.attachments
    .map((attachment) => attachment.id)
    .join(",")}`;
}

export type BusyComposerSubmitOptions = {
  state: BusyComposerState;
  event: { preventDefault: () => void; defaultPrevented?: boolean };
  tracker: BusyComposerSubmitTracker;
  finalizeImageAttachment: (
    attachment: PendingAttachment,
  ) => Promise<CompleteAttachment>;
  sendCommand: (command: BusyAddMessageCommand) => void | Promise<void>;
  resetComposer: (submittedFingerprint: string) => void | Promise<void>;
  onError: (error: unknown | null) => void;
  now?: () => number;
};

export type BusyComposerSubmitHandlerOptions = Omit<
  BusyComposerSubmitOptions,
  "state" | "event"
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
    if (!options.isRunning || event.defaultPrevented) return;
    return submitBusyComposerMessage({
      ...options,
      state: options.getState(),
      event,
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
    resetComposer,
    onError,
  } = options;
  if (event.defaultPrevented) return;
  if (!state.text.trim() && state.attachments.length === 0) return;

  const fingerprint = busyComposerFingerprint(state);
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
  onError(null);

  let messageSent = false;
  try {
    const parts: BusyMessagePart[] = [];
    if (state.text.trim()) parts.push({ type: "text", text: state.text });

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

    if (parts.length === 0) {
      tracker.last = null;
      return;
    }

    await sendCommand({
      type: "add-message",
      message: { role: "user", parts },
      parentId: null,
      sourceId: null,
    });
    messageSent = true;
    await resetComposer(fingerprint);
  } catch (error) {
    if (!messageSent) tracker.last = null;
    onError(error);
  } finally {
    tracker.inFlight.delete(fingerprint);
  }
}
