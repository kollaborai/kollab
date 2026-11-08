import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { imageAttachmentAdapter } from "./attachment-adapter";

const mocks = vi.hoisted(() => ({
  useAui: vi.fn(),
  useAuiState: vi.fn(),
  useAssistantTransportSendCommand: vi.fn(),
  useContext: vi.fn(),
  useMemo: vi.fn(),
  useRef: vi.fn(),
  useState: vi.fn(),
  usePendingPermissionCancellation: vi.fn(),
  useVoiceMode: vi.fn(),
  useRoomVoiceMode: vi.fn(),
  useVoiceRoomLive: vi.fn(),
}));

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    useContext: mocks.useContext,
    useMemo: mocks.useMemo,
    useRef: mocks.useRef,
    useState: mocks.useState,
  };
});

vi.mock("@assistant-ui/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@assistant-ui/react")>();
  return {
    ...actual,
    useAui: mocks.useAui,
    useAuiState: mocks.useAuiState,
    useAssistantTransportSendCommand: mocks.useAssistantTransportSendCommand,
  };
});

vi.mock("@/runtime", () => ({
  usePendingPermissionCancellation: mocks.usePendingPermissionCancellation,
}));

vi.mock("@/voice-mode", () => ({ useVoiceMode: mocks.useVoiceMode }));

vi.mock("@/voice/voice-room", () => ({
  useRoomVoiceMode: mocks.useRoomVoiceMode,
  useVoiceRoomLive: mocks.useVoiceRoomLive,
  VoiceRoomBar: () => null,
}));

import { Composer } from "./thread";
import { ComposerPrimitive } from "@assistant-ui/react";
import type { BusyComposerState } from "./busy-composer-submit";

const PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/xioAAAAASUVORK5CYII=";

async function pendingPng() {
  const bytes = Uint8Array.from(atob(PNG_BASE64), (character) =>
    character.charCodeAt(0),
  );
  return imageAttachmentAdapter.add({
    file: new File([bytes], "composer-synthetic.png", { type: "image/png" }),
  });
}

type ElementLike = { type: unknown; props: Record<string, unknown> };

function findElement(
  node: unknown,
  predicate: (element: ElementLike) => boolean,
): ElementLike | undefined {
  if (Array.isArray(node)) {
    for (const child of node) {
      const match = findElement(child, predicate);
      if (match) return match;
    }
    return undefined;
  }
  if (!node || typeof node !== "object" || !("type" in node) || !("props" in node)) {
    return undefined;
  }
  const element = node as ElementLike;
  if (predicate(element)) return element;
  return findElement(element.props.children, predicate);
}

function setupComposer(initialState: BusyComposerState) {
  let currentState = initialState;
  const sendCommand = vi.fn();
  const resetComposer = vi.fn(async () => {
    currentState = { text: "", attachments: [] };
  });
  const setBusySubmitError = vi.fn();
  const aui = {
    composer: {
      getState: () => currentState,
      reset: resetComposer,
      setText: vi.fn(),
    },
  };

  mocks.useContext
    .mockReset()
    .mockReturnValueOnce("")
    .mockReturnValueOnce(false)
    .mockReturnValueOnce({ workspace: null, remote: [] });
  mocks.useMemo.mockImplementation((factory: () => unknown) => factory());
  mocks.useRef.mockImplementation((current: unknown) => ({ current }));
  mocks.useState.mockReturnValue([null, setBusySubmitError]);
  mocks.useAui.mockReturnValue(aui);
  mocks.useAuiState.mockReset().mockReturnValueOnce("send").mockReturnValueOnce(true);
  mocks.useAssistantTransportSendCommand.mockReturnValue(sendCommand);
  mocks.usePendingPermissionCancellation.mockReturnValue(null);
  mocks.useVoiceMode.mockReturnValue({ on: false });
  mocks.useRoomVoiceMode.mockReturnValue(null);

  const tree = Composer({
    agents: [],
    commands: [],
    onOpenPanel: undefined,
    attachmentsEnabled: true,
  }) as unknown as ElementLike;
  const form = findElement(tree, (element) => element.type === ComposerPrimitive.Root);
  if (!form) throw new Error("Composer did not render its form root.");

  const slot = findElement(
    form,
    (element) =>
      typeof element.type === "function" && element.type.name === "ComposerSlot",
  );
  if (!slot) throw new Error("Composer did not render its send-button slot.");
  const slotTree = (slot.type as (props: Record<string, unknown>) => unknown)(slot.props);
  const steerButton = findElement(
    slotTree,
    (element) => element.props["aria-label"] === "Send message while generating",
  );
  if (!steerButton) throw new Error("Composer did not render its busy-send button.");

  return {
    form,
    steerButton,
    sendCommand,
    resetComposer,
    setBusySubmitError,
    setCurrentState: (state: BusyComposerState) => {
      currentState = state;
    },
    getCurrentState: () => currentState,
  };
}

function submitEvent() {
  return { preventDefault: vi.fn(), defaultPrevented: false };
}

type SubmitEvent = ReturnType<typeof submitEvent>;

describe("thread Composer busy-send wiring", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("form submit finalizes the pending PNG and sends one image-bearing command", async () => {
    const attachment = await pendingPng();
    const harness = setupComposer({ text: "describe this", attachments: [attachment] });
    const finalize = vi.spyOn(imageAttachmentAdapter, "send");
    const event = submitEvent();
    expect(harness.form.props.onSubmit).toHaveProperty(
      "name",
      "handleComposerSubmit",
    );

    await (harness.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(event);

    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(finalize).toHaveBeenCalledOnce();
    expect(harness.sendCommand).toHaveBeenCalledOnce();
    expect(harness.sendCommand.mock.calls[0][0]).toMatchObject({
      type: "add-message",
      message: {
        role: "user",
        parts: [
          { type: "text", text: "describe this" },
          { type: "image", image: `data:image/png;base64,${PNG_BASE64}` },
        ],
      },
    });
    expect(harness.resetComposer).toHaveBeenCalledOnce();
  });

  it("the actual busy-send button submits an image-only draft", async () => {
    const attachment = await pendingPng();
    const harness = setupComposer({ text: "", attachments: [attachment] });
    const finalize = vi.spyOn(imageAttachmentAdapter, "send");
    const event = submitEvent();
    expect(harness.steerButton.props.onClick).toHaveProperty(
      "name",
      "submitBusyMessage",
    );

    await (harness.steerButton.props.onClick as (event: SubmitEvent) => Promise<void>)(event);

    expect(finalize).toHaveBeenCalledOnce();
    expect(harness.sendCommand).toHaveBeenCalledOnce();
    expect(harness.sendCommand.mock.calls[0][0]).toMatchObject({
      message: {
        parts: [{ type: "image", image: `data:image/png;base64,${PNG_BASE64}` }],
      },
    });
    expect(harness.resetComposer).toHaveBeenCalledOnce();
  });

  it("does not reset edits made while image conversion is pending", async () => {
    const attachment = await pendingPng();
    const harness = setupComposer({ text: "original", attachments: [attachment] });
    const complete = await imageAttachmentAdapter.send(attachment);
    let finishConversion!: (value: typeof complete) => void;
    const pendingConversion = new Promise<typeof complete>((resolve) => {
      finishConversion = resolve;
    });
    vi.spyOn(imageAttachmentAdapter, "send").mockReturnValueOnce(pendingConversion);
    const event = submitEvent();

    const submitted = (harness.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(event);
    harness.setCurrentState({ text: "edited while converting", attachments: [attachment] });
    finishConversion(complete);
    await submitted;

    expect(harness.sendCommand.mock.calls[0][0]).toMatchObject({
      message: {
        parts: [
          { type: "text", text: "original" },
          { type: "image", image: `data:image/png;base64,${PNG_BASE64}` },
        ],
      },
    });
    expect(harness.resetComposer).not.toHaveBeenCalled();
    expect(harness.getCurrentState().text).toBe("edited while converting");
  });

  it("keeps the draft and reports a conversion failure without sending", async () => {
    const attachment = await pendingPng();
    const harness = setupComposer({ text: "retain this", attachments: [attachment] });
    vi.spyOn(imageAttachmentAdapter, "send").mockRejectedValueOnce(new Error("read failed"));

    await (harness.form.props.onSubmit as (event: ReturnType<typeof submitEvent>) => Promise<void>)(submitEvent());

    expect(harness.sendCommand).not.toHaveBeenCalled();
    expect(harness.resetComposer).not.toHaveBeenCalled();
    expect(harness.getCurrentState()).toEqual({ text: "retain this", attachments: [attachment] });
    expect(harness.setBusySubmitError).toHaveBeenLastCalledWith(
      "Could not prepare the image. Your draft is still here; try sending again.",
    );
  });

  it("keeps the draft and reports transport failure separately from conversion", async () => {
    const attachment = await pendingPng();
    const harness = setupComposer({ text: "retain after transport failure", attachments: [attachment] });
    harness.sendCommand.mockRejectedValueOnce(new Error("transport failed"));

    await (harness.form.props.onSubmit as (event: ReturnType<typeof submitEvent>) => Promise<void>)(submitEvent());

    expect(harness.sendCommand).toHaveBeenCalledOnce();
    expect(harness.resetComposer).not.toHaveBeenCalled();
    expect(harness.getCurrentState().text).toBe("retain after transport failure");
    expect(harness.setBusySubmitError).toHaveBeenLastCalledWith(
      "Could not send this message. Your draft is still here; try sending again.",
    );
  });

  it("does not invite retry after transport success when reset fails", async () => {
    const attachment = await pendingPng();
    const harness = setupComposer({ text: "already sent", attachments: [attachment] });
    harness.resetComposer.mockRejectedValueOnce(new Error("reset failed"));
    const firstEvent = submitEvent();

    await (harness.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(firstEvent);
    expect(harness.sendCommand).toHaveBeenCalledOnce();
    expect(harness.setBusySubmitError).toHaveBeenLastCalledWith(
      "Message sent, but the draft could not be cleared. Clear or edit it before submitting again.",
    );

    const retryEvent = submitEvent();
    await (harness.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(retryEvent);

    expect(retryEvent.preventDefault).toHaveBeenCalledOnce();
    expect(harness.sendCommand).toHaveBeenCalledOnce();
    expect(harness.setBusySubmitError).toHaveBeenLastCalledWith(
      "This draft was already sent. Clear or edit it before submitting again.",
    );
  });
});
