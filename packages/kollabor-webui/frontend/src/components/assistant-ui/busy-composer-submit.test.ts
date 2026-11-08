import { describe, expect, it, vi } from "vitest";
import { imageAttachmentAdapter } from "./attachment-adapter";
import {
  createBusyComposerSubmitTracker,
  submitBusyComposerMessage,
  type BusyComposerState,
} from "./busy-composer-submit";

const PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/xioAAAAASUVORK5CYII=";
const PNG_BYTES = Uint8Array.from(atob(PNG_BASE64), (character) =>
  character.charCodeAt(0),
);

async function pendingPng() {
  const file = new File(
    [PNG_BYTES],
    "synthetic.png",
    { type: "image/png" },
  );
  return imageAttachmentAdapter.add({ file });
}

function attempt(
  state: BusyComposerState,
  overrides: Partial<Parameters<typeof submitBusyComposerMessage>[0]> = {},
) {
  const sendCommand = vi.fn();
  const resetComposer = vi.fn();
  const onError = vi.fn();
  const event = { preventDefault: vi.fn() };
  const tracker = createBusyComposerSubmitTracker();
  const options: Parameters<typeof submitBusyComposerMessage>[0] = {
    state,
    event,
    tracker,
    finalizeImageAttachment: (attachment: Parameters<typeof imageAttachmentAdapter.send>[0]) =>
      imageAttachmentAdapter.send(attachment),
    sendCommand,
    resetComposer,
    onError,
    ...overrides,
  };
  return { options, sendCommand, resetComposer, onError, event, tracker };
}

describe("busy composer submit", () => {
  it("sends text with finalized pending image content", async () => {
    const attachment = await pendingPng();
    const test = attempt({ text: "describe this", attachments: [attachment] });

    await submitBusyComposerMessage(test.options);

    expect(test.sendCommand).toHaveBeenCalledTimes(1);
    expect(test.sendCommand.mock.calls[0][0]).toMatchObject({
      type: "add-message",
      message: {
        role: "user",
        parts: [
          { type: "text", text: "describe this" },
          { type: "image", image: `data:image/png;base64,${PNG_BASE64}` },
        ],
      },
    });
    expect(test.resetComposer).toHaveBeenCalledTimes(1);
    expect(test.onError).toHaveBeenLastCalledWith(null);
  });

  it("sends a pending image without text", async () => {
    const attachment = await pendingPng();
    const test = attempt({ text: "", attachments: [attachment] });

    await submitBusyComposerMessage(test.options);

    expect(test.sendCommand).toHaveBeenCalledTimes(1);
    expect(test.sendCommand.mock.calls[0][0]).toMatchObject({
      message: {
        parts: [{ type: "image", image: `data:image/png;base64,${PNG_BASE64}` }],
      },
    });
    expect(test.resetComposer).toHaveBeenCalledTimes(1);
  });

  it("preserves text-only sends and suppresses immediate duplicates", async () => {
    const state: BusyComposerState = { text: "  keep this text  ", attachments: [] };
    const test = attempt(state);

    await submitBusyComposerMessage(test.options);
    const duplicateEvent = { preventDefault: vi.fn() };
    await submitBusyComposerMessage({
      ...test.options,
      event: duplicateEvent,
    });

    expect(test.sendCommand).toHaveBeenCalledTimes(1);
    expect(test.sendCommand.mock.calls[0][0]).toMatchObject({
      message: { parts: [{ type: "text", text: "  keep this text  " }] },
    });
    expect(test.resetComposer).toHaveBeenCalledTimes(1);
    expect(duplicateEvent.preventDefault).toHaveBeenCalledTimes(1);
  });

  it("keeps the draft and surfaces adapter conversion failures", async () => {
    const attachment = await pendingPng();
    const state: BusyComposerState = { text: "retain me", attachments: [attachment] };
    const conversionError = new Error("synthetic conversion failure");
    const test = attempt(state, {
      finalizeImageAttachment: vi.fn().mockRejectedValue(conversionError),
    });

    await submitBusyComposerMessage(test.options);

    expect(test.sendCommand).not.toHaveBeenCalled();
    expect(test.resetComposer).not.toHaveBeenCalled();
    expect(test.onError).toHaveBeenLastCalledWith(conversionError);
    expect(state).toEqual({ text: "retain me", attachments: [attachment] });
  });

  it("does not start a second conversion while the same send is pending", async () => {
    const attachment = await pendingPng();
    const complete = await imageAttachmentAdapter.send(attachment);
    let release!: (value: typeof complete) => void;
    const conversion = new Promise<typeof complete>((resolve) => {
      release = resolve;
    });
    const finalizeImageAttachment = vi.fn(() => conversion);
    const test = attempt(
      { text: "one send", attachments: [attachment] },
      { finalizeImageAttachment },
    );

    const first = submitBusyComposerMessage(test.options);
    const duplicateEvent = { preventDefault: vi.fn() };
    const second = submitBusyComposerMessage({
      ...test.options,
      event: duplicateEvent,
    });
    expect(finalizeImageAttachment).toHaveBeenCalledTimes(1);
    expect(duplicateEvent.preventDefault).toHaveBeenCalledTimes(1);

    release(complete);
    await Promise.all([first, second]);

    expect(test.sendCommand).toHaveBeenCalledTimes(1);
    expect(test.resetComposer).toHaveBeenCalledTimes(1);
  });
});
