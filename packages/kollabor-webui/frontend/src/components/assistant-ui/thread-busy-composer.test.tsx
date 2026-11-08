import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { imageAttachmentAdapter } from "./attachment-adapter";

const mocks = vi.hoisted(() => ({
  useAui: vi.fn(),
  useAuiState: vi.fn(),
  useAssistantTransportSendCommand: vi.fn(),
  useContext: vi.fn(),
  useEffect: vi.fn(),
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
    useEffect: mocks.useEffect,
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
import {
  createBusyComposerSubmitTracker,
  type BusyComposerState,
  type BusyComposerSubmitTracker,
} from "./busy-composer-submit";

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

function setupComposer(
  initialState: BusyComposerState,
  options: {
    isRunning?: boolean;
    tracker?: BusyComposerSubmitTracker;
    busySubmitError?: string | null;
  } = {},
) {
  let currentState = initialState;
  let currentError = options.busySubmitError ?? null;
  const sendCommand = vi.fn();
  const resetComposer = vi.fn(async () => {
    currentState = { text: "", attachments: [] };
    notifyComposerChange();
  });
  const setBusySubmitError = vi.fn((error: string | null) => {
    currentError = error;
  });
  const tracker = options.tracker ?? createBusyComposerSubmitTracker();
  const listeners = new Set<() => void>();
  const notifyComposerChange = () => {
    for (const listener of listeners) listener();
  };
  const aui = {
    subscribe: vi.fn((listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    }),
    composer: {
      getState: () => currentState,
      reset: resetComposer,
      setText: vi.fn(),
    },
  };

  let effectIndex = 0;
  const effects: Array<{
    dependencies: readonly unknown[];
    cleanup?: () => void;
  }> = [];
  mocks.useEffect.mockImplementation(
    (
      effect: () => void | (() => void),
      dependencies: readonly unknown[] = [],
    ) => {
      const index = effectIndex++;
      const previous = effects[index];
      const changed =
        !previous ||
        previous.dependencies.length !== dependencies.length ||
        dependencies.some((dependency, i) => dependency !== previous.dependencies[i]);
      if (!changed) return;
      previous?.cleanup?.();
      const cleanup = effect();
      effects[index] = {
        dependencies: [...dependencies],
        cleanup: typeof cleanup === "function" ? cleanup : undefined,
      };
    },
  );

  const render = (isRunning = options.isRunning ?? true) => {
    effectIndex = 0;
    mocks.useContext
      .mockReset()
      .mockReturnValueOnce("")
      .mockReturnValueOnce(false)
      .mockReturnValueOnce({ workspace: null, remote: [] });
    mocks.useMemo.mockImplementation((factory: () => unknown) => factory());
    mocks.useRef.mockReturnValue({ current: tracker });
    mocks.useState.mockReturnValue([currentError, setBusySubmitError]);
    mocks.useAui.mockReturnValue(aui);
    mocks.useAuiState
      .mockReset()
      .mockReturnValueOnce("send")
      .mockReturnValueOnce(isRunning);
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
      (element) =>
        element.props["aria-label"] ===
        (isRunning ? "Send message while generating" : "Send message"),
    );
    if (!steerButton) throw new Error("Composer did not render its send button.");

    return { form, steerButton };
  };

  const rendered = render(options.isRunning ?? true);

  return {
    ...rendered,
    render,
    sendCommand,
    resetComposer,
    setBusySubmitError,
    setCurrentState: (state: BusyComposerState) => {
      currentState = state;
      notifyComposerChange();
    },
    getCurrentState: () => currentState,
    getBusySubmitError: () => currentError,
    tracker,
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

  it("does not reset the draft if it changes and returns to the same text during conversion", async () => {
    const attachment = await pendingPng();
    const draft = { text: "original", attachments: [attachment] };
    const harness = setupComposer(draft);
    const complete = await imageAttachmentAdapter.send(attachment);
    let finishConversion!: (value: typeof complete) => void;
    const pendingConversion = new Promise<typeof complete>((resolve) => {
      finishConversion = resolve;
    });
    vi.spyOn(imageAttachmentAdapter, "send").mockReturnValueOnce(pendingConversion);

    const submitted = (harness.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(
      submitEvent(),
    );
    harness.setCurrentState({ text: "", attachments: [] });
    harness.setCurrentState(draft);
    finishConversion(complete);
    await submitted;

    expect(harness.sendCommand).toHaveBeenCalledOnce();
    expect(harness.resetComposer).not.toHaveBeenCalled();
    expect(harness.getCurrentState()).toEqual(draft);
  });

  it.each(["added", "removed"] as const)(
    "does not reset the composer when an attachment is %s while conversion is pending",
    async (change) => {
      const attachment = await pendingPng();
      const additionalAttachment = await pendingPng();
      const harness = setupComposer({ text: "describe this", attachments: [attachment] });
      const complete = await imageAttachmentAdapter.send(attachment);
      let finishConversion!: (value: typeof complete) => void;
      const pendingConversion = new Promise<typeof complete>((resolve) => {
        finishConversion = resolve;
      });
      vi.spyOn(imageAttachmentAdapter, "send").mockReturnValueOnce(pendingConversion);

      const submitted = (harness.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(
        submitEvent(),
      );
      harness.setCurrentState({
        text: "describe this",
        attachments:
          change === "added"
            ? [attachment, additionalAttachment]
            : [],
      });
      finishConversion(complete);
      await submitted;

      expect(harness.sendCommand).toHaveBeenCalledOnce();
      expect(harness.sendCommand.mock.calls[0][0]).toMatchObject({
        message: {
          parts: [
            { type: "text", text: "describe this" },
            { type: "image", image: `data:image/png;base64,${PNG_BASE64}` },
          ],
        },
      });
      expect(harness.resetComposer).not.toHaveBeenCalled();
      expect(harness.getCurrentState().attachments).toEqual(
        change === "added" ? [attachment, additionalAttachment] : [],
      );
    },
  );

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

  it("allows the same draft to retry after a rejected transport send", async () => {
    const attachment = await pendingPng();
    const harness = setupComposer({
      text: "retry after rejection",
      attachments: [attachment],
    });
    harness.sendCommand.mockRejectedValueOnce(new Error("transport failed"));

    await (harness.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(
      submitEvent(),
    );
    expect(harness.sendCommand).toHaveBeenCalledOnce();
    expect(harness.resetComposer).not.toHaveBeenCalled();
    expect(harness.getCurrentState()).toEqual({
      text: "retry after rejection",
      attachments: [attachment],
    });
    expect(harness.getBusySubmitError()).toBe(
      "Could not send this message. Your draft is still here; try sending again.",
    );

    await (harness.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(
      submitEvent(),
    );

    expect(harness.sendCommand).toHaveBeenCalledTimes(2);
    expect(harness.resetComposer).toHaveBeenCalledOnce();
    expect(harness.getCurrentState()).toEqual({ text: "", attachments: [] });
    expect(harness.getBusySubmitError()).toBeNull();
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

  it("clears a transient busy-send error at idle and lets the ordinary submit proceed", async () => {
    const attachment = await pendingPng();
    const harness = setupComposer({ text: "retry when idle", attachments: [attachment] });
    harness.sendCommand.mockRejectedValueOnce(new Error("transport failed"));

    await (harness.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(
      submitEvent(),
    );
    expect(harness.getBusySubmitError()).toBe(
      "Could not send this message. Your draft is still here; try sending again.",
    );

    const idleComposer = harness.render(false);
    expect(harness.getBusySubmitError()).toBeNull();
    const idleEvent = submitEvent();
    await (idleComposer.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(
      idleEvent,
    );

    expect(idleEvent.preventDefault).not.toHaveBeenCalled();
    expect(harness.sendCommand).toHaveBeenCalledOnce();
    expect(harness.getCurrentState()).toEqual({
      text: "retry when idle",
      attachments: [attachment],
    });
  });

  it("keeps the sent-but-not-cleared warning after the turn becomes idle", async () => {
    const attachment = await pendingPng();
    const harness = setupComposer({ text: "sent but retained", attachments: [attachment] });
    harness.resetComposer.mockRejectedValueOnce(new Error("reset failed"));

    await (harness.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(
      submitEvent(),
    );
    const resetWarning =
      "Message sent, but the draft could not be cleared. Clear or edit it before submitting again.";
    expect(harness.getBusySubmitError()).toBe(resetWarning);

    const idleComposer = harness.render(false);
    expect(harness.getBusySubmitError()).toBe(resetWarning);
    const idleEvent = submitEvent();
    await (idleComposer.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(
      idleEvent,
    );

    expect(idleEvent.preventDefault).toHaveBeenCalledOnce();
    expect(harness.sendCommand).toHaveBeenCalledOnce();
    expect(harness.getBusySubmitError()).toBe(
      "This draft was already sent. Clear or edit it before submitting again.",
    );
  });

  it("allows the same intentional draft after a clear and retype, while blocking an immediate duplicate", async () => {
    const draft = { text: "send intentionally again", attachments: [] };
    const harness = setupComposer(draft);
    harness.resetComposer.mockRejectedValueOnce(new Error("reset failed"));

    await (harness.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(
      submitEvent(),
    );
    const duplicateEvent = submitEvent();
    await (harness.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(
      duplicateEvent,
    );
    expect(duplicateEvent.preventDefault).toHaveBeenCalledOnce();
    expect(harness.sendCommand).toHaveBeenCalledOnce();

    harness.setCurrentState({ text: "", attachments: [] });
    expect(harness.getBusySubmitError()).toBeNull();
    harness.setCurrentState(draft);

    await (harness.form.props.onSubmit as (event: SubmitEvent) => Promise<void>)(
      submitEvent(),
    );

    expect(harness.sendCommand).toHaveBeenCalledTimes(2);
    expect(harness.resetComposer).toHaveBeenCalledTimes(2);
    expect(harness.getCurrentState()).toEqual({ text: "", attachments: [] });
  });
});
