import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { SimpleImageAttachmentAdapter } from "@assistant-ui/react";

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
) {
  let currentState = state;
  const sendTransportCommand = vi.fn();
  const reset = vi.fn();
  const setBusySubmitError = vi.fn();
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
    // A plain send: the message waits for the turn to end (#1102).
    submitSendsNow: { current: false },
    sendWaitingNow: vi.fn(),
    imageAttachmentAdapter: attachmentAdapter,
    setBusySubmitError,
  });
  return {
    sendBusyMessage,
    sendTransportCommand,
    reset,
    setBusySubmitError,
    setState: (nextState: { text: string; attachments: unknown[] }) => {
      currentState = nextState;
    },
  };
}

describe("sendBusyMessage pending image attachments", () => {
  it("sends a pending image together with text while a turn is running", async () => {
    const image = await pendingImage();
    expect(image.content).toBeUndefined();
    const { sendBusyMessage, sendTransportCommand, reset } = harness({
      text: "inspect",
      attachments: [image],
    });
    const event = { preventDefault: vi.fn() };

    await sendBusyMessage(event);

    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(sendTransportCommand).toHaveBeenCalledOnce();
    expect(sendTransportCommand.mock.calls[0][0].message.parts).toEqual([
      { type: "text", text: "inspect" },
      expect.objectContaining({ type: "image", image: expect.any(String) }),
    ]);
    expect(reset).toHaveBeenCalledOnce();
  });

  it("sends an image-only pending attachment while a turn is running", async () => {
    const image = await pendingImage();
    const { sendBusyMessage, sendTransportCommand, reset } = harness({
      text: "",
      attachments: [image],
    });
    const event = { preventDefault: vi.fn() };

    await sendBusyMessage(event);

    expect(sendTransportCommand).toHaveBeenCalledOnce();
    expect(sendTransportCommand.mock.calls[0][0].message.parts).toEqual([
      expect.objectContaining({ type: "image", image: expect.any(String) }),
    ]);
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
    const { sendBusyMessage, sendTransportCommand } = harness(
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
    expect(sendTransportCommand).toHaveBeenCalledOnce();
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
    const { sendBusyMessage, sendTransportCommand, reset, setState } = harness(
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

    expect(sendTransportCommand.mock.calls[0][0].message.parts).toEqual([
      { type: "text", text: "inspect" },
      expect.objectContaining({ type: "image", image: expect.any(String) }),
    ]);
    expect(reset).not.toHaveBeenCalled();
  });

  it("keeps text-only submission and duplicate suppression working", async () => {
    const { sendBusyMessage, sendTransportCommand, reset } = harness({
      text: "inspect",
      attachments: [],
    });
    const firstEvent = { preventDefault: vi.fn() };
    const duplicateEvent = { preventDefault: vi.fn() };

    await sendBusyMessage(firstEvent);
    await sendBusyMessage(duplicateEvent);

    expect(sendTransportCommand).toHaveBeenCalledOnce();
    expect(sendTransportCommand.mock.calls[0][0].message.parts).toEqual([
      { type: "text", text: "inspect" },
    ]);
    expect(reset).toHaveBeenCalledOnce();
    expect(duplicateEvent.preventDefault).toHaveBeenCalledOnce();
  });

  it("preserves attachments and shows an actionable error when conversion fails", async () => {
    const image = await pendingImage();
    const attachmentAdapter = {
      send: vi.fn().mockRejectedValue(new Error("image read failed")),
    };
    const { sendBusyMessage, sendTransportCommand, reset, setBusySubmitError } =
      harness({ text: "inspect", attachments: [image] }, attachmentAdapter);

    await sendBusyMessage({ preventDefault: vi.fn() });

    expect(sendTransportCommand).not.toHaveBeenCalled();
    expect(reset).not.toHaveBeenCalled();
    expect(setBusySubmitError).toHaveBeenCalledWith(
      expect.stringMatching(/retry|remove/i),
    );
  });
});
