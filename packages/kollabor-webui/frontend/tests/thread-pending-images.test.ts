import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { SimpleImageAttachmentAdapter } from "@assistant-ui/react";
import {
  acknowledgeBusyWebMessage as acknowledgeQueueMessage,
  type BusyWebMessageResult,
  type QueueSubmissionLocks,
} from "../src/components/web-queue/submit.ts";

const productionFile = new URL(
  "../src/components/assistant-ui/thread.tsx",
  import.meta.url,
);

function loadSendBusyMessage(dependencies: Record<string, unknown>) {
  const source = readFileSync(productionFile, "utf8");
  const start = source.indexOf("const sendBusyMessage = ");
  const end = source.indexOf("\n  const submitBusyMessage", start);
  if (start < 0 || end < 0) throw new Error("sendBusyMessage was not found");
  const declaration = source
    .slice(start, end)
    .replace(/const sendBusyMessage = (async )?\(event: \{[\s\S]*?\}\) => \{/, "const sendBusyMessage = $1(event) => {")
    .replace(/ as PendingAttachment/g, "")
    .replace(/ as ComposerAttachmentLike\[\]/g, "")
    .replace(/const parts: Array<[\s\S]*?\n\s*> = \[\];/, "const parts = [];");
  const compiled = declaration
    .replace("const sendBusyMessage = ", "")
    .replace(/;\s*$/, "");
  const names = Object.keys(dependencies);
  const build = new Function(...names, `return ${compiled}`) as (
    ...args: unknown[]
  ) => (event: { preventDefault: () => void; defaultPrevented?: boolean }) => void;
  return build(...Object.values(dependencies));
}

async function pendingImage() {
  const png = Uint8Array.from(
    Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQ4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==",
      "base64",
    ),
  );
  const adapter = new SimpleImageAttachmentAdapter();
  return adapter.add({
    file: new File([png], "synthetic.png", { type: "image/png" }),
  });
}

function harness(
  state: { text: string; attachments: unknown[] },
  attachmentAdapter = new SimpleImageAttachmentAdapter(),
  sendNow = false,
  acknowledge: (result: BusyWebMessageResult) => Promise<boolean> | boolean = () => true,
) {
  let currentState = state;
  const sendTransportCommand = vi.fn();
  const reset = vi.fn();
  const setBusySubmitError = vi.fn();
  const setQueueAdmissionLock = vi.fn();
  const enqueueBusyMessage = vi.fn(async (input: { sendNow: boolean }) => ({
    admitted: true as const,
    batchId: "batch-1",
    requestId: "request-1",
    operationId: "operation-1",
    scopeId: "scope-1",
    incarnation: "incarnation-1",
    planKey: "plan-1",
    sendNowStatus: input.sendNow ? "accepted" as const : "not-requested" as const,
  }));
  const acknowledgeBusyWebMessage = vi.fn(acknowledge);
  const saveComposerDraft = vi.fn();
  const lastBusySubmit = { current: undefined as unknown };
  const skipEmptyDraftPersist = { current: false };
  const sendBusyMessage = loadSendBusyMessage({
    isRunning: true,
    aui: { composer: { getState: () => currentState, reset } },
    lastBusySubmit,
    draftKey: "test-draft",
    queueComposerDraftSubmission: vi.fn(),
    skipEmptyDraftPersist,
    sendTransportCommand,
    onQueueBusyMessage: enqueueBusyMessage,
    acknowledgeBusyWebMessage,
    saveComposerDraft,
    submitSendsNow: { current: sendNow },
    sendWaitingNow: vi.fn(),
    imageAttachmentAdapter: attachmentAdapter,
    setBusySubmitError,
    setQueueAdmissionLock,
  });
  return {
    sendBusyMessage,
    sendTransportCommand,
    enqueueBusyMessage,
    acknowledgeBusyWebMessage,
    reset,
    setBusySubmitError,
    setQueueAdmissionLock,
    setState: (nextState: { text: string; attachments: unknown[] }) => {
      currentState = nextState;
    },
  };
}

describe("sendBusyMessage pending image attachments", () => {
  it("uses the daemon queue for busy composer sends and never sends a legacy add-message", async () => {
    const image = await pendingImage();
    const { sendBusyMessage, sendTransportCommand, enqueueBusyMessage, reset } = harness(
      { text: "inspect", attachments: [image] },
      new SimpleImageAttachmentAdapter(),
      true,
    );
    const event = { preventDefault: vi.fn() };

    await sendBusyMessage(event);

    expect(enqueueBusyMessage).toHaveBeenCalledOnce();
    expect(enqueueBusyMessage.mock.calls[0]?.[0]).toMatchObject({
      text: "inspect",
      sendNow: true,
      attachments: [expect.objectContaining({ name: "synthetic.png", type: "image" })],
    });
    expect(sendTransportCommand).not.toHaveBeenCalled();
    expect(reset).toHaveBeenCalledOnce();
  });

  it("sends a pending image together with text while a turn is running", async () => {
    const image = await pendingImage();
    expect(image.content).toBeUndefined();
    const { sendBusyMessage, sendTransportCommand, enqueueBusyMessage, reset } = harness({
      text: "inspect",
      attachments: [image],
    });
    const event = { preventDefault: vi.fn() };

    await sendBusyMessage(event);

    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(sendTransportCommand).not.toHaveBeenCalled();
    expect(enqueueBusyMessage).toHaveBeenCalledOnce();
    expect(enqueueBusyMessage.mock.calls[0]?.[0]).toMatchObject({
      text: "inspect",
      attachments: [expect.objectContaining({ name: "synthetic.png", type: "image", content: expect.any(Array) })],
    });
    expect(reset).toHaveBeenCalledOnce();
  });

  it("sends an image-only pending attachment while a turn is running", async () => {
    const image = await pendingImage();
    const { sendBusyMessage, sendTransportCommand, enqueueBusyMessage, reset } = harness({
      text: "",
      attachments: [image],
    });
    const event = { preventDefault: vi.fn() };

    await sendBusyMessage(event);

    expect(sendTransportCommand).not.toHaveBeenCalled();
    expect(enqueueBusyMessage).toHaveBeenCalledOnce();
    expect(enqueueBusyMessage.mock.calls[0]?.[0]).toMatchObject({
      text: "",
      attachments: [expect.objectContaining({ name: "synthetic.png", type: "image", content: expect.any(Array) })],
    });
    expect(reset).toHaveBeenCalledOnce();
  });

  it("suppresses duplicates while a pending image is still converting", async () => {
    const image = await pendingImage();
    const attachmentAdapter = new SimpleImageAttachmentAdapter();
    let resolveConversion!: (
      value: Awaited<ReturnType<typeof attachmentAdapter.send>>,
    ) => void;
    vi.spyOn(attachmentAdapter, "send").mockReturnValue(
      new Promise((resolve) => {
        resolveConversion = resolve;
      }),
    );
    const { sendBusyMessage, sendTransportCommand, enqueueBusyMessage } = harness(
      { text: "inspect", attachments: [image] },
      attachmentAdapter,
    );
    vi.spyOn(Date, "now")
      .mockReturnValueOnce(1000)
      .mockReturnValueOnce(1501);

    const first = sendBusyMessage({ preventDefault: vi.fn() });
    const duplicate = sendBusyMessage({ preventDefault: vi.fn() });
    resolveConversion({
      ...image,
      content: [{ type: "image", image: "data:image/png;base64,cG5n" }],
    });
    await Promise.all([first, duplicate]);

    expect(attachmentAdapter.send).toHaveBeenCalledOnce();
    expect(sendTransportCommand).not.toHaveBeenCalled();
    expect(enqueueBusyMessage).toHaveBeenCalledOnce();
    vi.restoreAllMocks();
  });

  it("preserves composer edits made while a pending image is converting", async () => {
    const image = await pendingImage();
    const attachmentAdapter = new SimpleImageAttachmentAdapter();
    let resolveConversion!: (
      value: Awaited<ReturnType<typeof attachmentAdapter.send>>,
    ) => void;
    vi.spyOn(attachmentAdapter, "send").mockReturnValue(
      new Promise((resolve) => {
        resolveConversion = resolve;
      }),
    );
    const { sendBusyMessage, sendTransportCommand, enqueueBusyMessage, reset, setState } = harness(
      { text: "inspect", attachments: [image] },
      attachmentAdapter,
    );

    const submission = sendBusyMessage({ preventDefault: vi.fn() });
    setState({ text: "new message", attachments: [image] });
    resolveConversion({
      ...image,
      content: [{ type: "image", image: "data:image/png;base64,cG5n" }],
    });
    await submission;

    expect(sendTransportCommand).not.toHaveBeenCalled();
    expect(enqueueBusyMessage.mock.calls[0]?.[0]).toMatchObject({ text: "inspect" });
    expect(reset).not.toHaveBeenCalled();
  });

  it("keeps text-only submission and duplicate suppression working", async () => {
    const { sendBusyMessage, sendTransportCommand, enqueueBusyMessage, reset } = harness({
      text: "inspect",
      attachments: [],
    });
    const firstEvent = { preventDefault: vi.fn() };
    const duplicateEvent = { preventDefault: vi.fn() };

    await sendBusyMessage(firstEvent);
    await sendBusyMessage(duplicateEvent);

    expect(sendTransportCommand).not.toHaveBeenCalled();
    expect(enqueueBusyMessage).toHaveBeenCalledOnce();
    expect(reset).toHaveBeenCalledOnce();
    expect(duplicateEvent.preventDefault).toHaveBeenCalledOnce();
  });

  it("preserves attachments and shows an actionable error when conversion fails", async () => {
    const image = await pendingImage();
    const attachmentAdapter = {
      send: vi.fn().mockRejectedValue(new Error("image read failed")),
    };
    const { sendBusyMessage, sendTransportCommand, enqueueBusyMessage, reset, setBusySubmitError, setQueueAdmissionLock } =
      harness({ text: "inspect", attachments: [image] }, attachmentAdapter);

    await sendBusyMessage({ preventDefault: vi.fn() });

    expect(sendTransportCommand).not.toHaveBeenCalled();
    expect(enqueueBusyMessage).not.toHaveBeenCalled();
    expect(reset).not.toHaveBeenCalled();
    expect(setBusySubmitError).toHaveBeenCalledWith(
      expect.stringMatching(/confirm queue admission|retry/i),
    );
    expect(setQueueAdmissionLock.mock.calls.at(-1)?.[0]).toEqual({ draftKey: "test-draft", locked: false });
  });

  it("locks an ambiguous submitted draft for exact retry without clearing its content", async () => {
    const { sendBusyMessage, enqueueBusyMessage, reset, setQueueAdmissionLock, setBusySubmitError } = harness({
      text: "inspect exactly this",
      attachments: [],
    });
    enqueueBusyMessage.mockRejectedValueOnce(Object.assign(
      new Error("response lost"),
      { requiresExactRetry: true },
    ));

    await sendBusyMessage({ preventDefault: vi.fn() });

    expect(reset).not.toHaveBeenCalled();
    expect(setQueueAdmissionLock.mock.calls.at(-1)?.[0]).toEqual({ draftKey: "test-draft", locked: true });
    expect(setBusySubmitError).toHaveBeenCalledWith(expect.stringMatching(/exact message unchanged and retry/));
  });

  it("reports confirmed admission and unlocks after asynchronous ack-lock denial", async () => {
    let rejectLock!: (reason?: unknown) => void;
    const lockGate = new Promise<void>((_resolve, reject) => { rejectLock = reject; });
    const locks: QueueSubmissionLocks = {
      request: async <T>() => {
        await lockGate;
        throw new Error("NotAllowedError");
      },
    };
    const storage = {
      getItem: () => null,
      setItem: () => undefined,
      removeItem: () => undefined,
    };
    const acknowledge = (result: BusyWebMessageResult) =>
      acknowledgeQueueMessage(result, storage, locks);
    const { sendBusyMessage, reset, setBusySubmitError, setQueueAdmissionLock } = harness(
      { text: "already queued", attachments: [] },
      new SimpleImageAttachmentAdapter(),
      false,
      acknowledge,
    );

    const sending = sendBusyMessage({ preventDefault: vi.fn() });
    rejectLock(new Error("lock request denied asynchronously"));
    await sending;

    expect(reset).toHaveBeenCalledOnce();
    expect(setBusySubmitError).toHaveBeenLastCalledWith(
      "Message was queued, but the local retry guard could not be cleared. Check the queue before reusing this draft.",
    );
    expect(setQueueAdmissionLock.mock.calls.at(-1)?.[0]).toEqual({ draftKey: "test-draft", locked: false });
  });
});
