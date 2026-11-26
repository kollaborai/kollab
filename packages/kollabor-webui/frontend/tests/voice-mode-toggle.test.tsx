// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EngineApi } from "@/api";
import type { InputInfo } from "@/chat-stream";
import type { LiveStore } from "@/chat-store";
import { useVoiceMode, VoiceModeProvider, type VoiceMode } from "@/voice-mode";

// The toggle must not reach the chat runtime. The mock records any call through
// the thread API, so a toggle that still sends through it fails these tests.
const thread = vi.hoisted(() => ({ append: vi.fn() }));
vi.mock("@assistant-ui/react", () => ({
  useAui: () => ({ thread: { append: thread.append } }),
  useAuiState: (selector: (state: unknown) => unknown) => selector({ thread: { isRunning: false } }),
}));

type Send = (text: string) => void | Promise<void>;

/** A fake EngineApi: getSessionState reports `system`, read live from the object. */
function fakeApi(system: Record<string, unknown>) {
  return {
    system,
    getSessionState: vi.fn(async () => ({ system: { ...system } })),
    getHistory: vi.fn(async () => ({ history: [] as unknown[] })),
  };
}
type FakeApi = ReturnType<typeof fakeApi>;

let latest!: VoiceMode;
function Probe() {
  latest = useVoiceMode();
  const error = latest.error;
  return error ? <div role="alert">{error}</div> : null;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  thread.append.mockReset();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

/** Lets the reads the provider starts settle, then flushes the state they set. */
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function fakeInputStore() {
  let inputs: Record<string, InputInfo> = {};
  const listeners = new Set<() => void>();
  const store = {
    get inputs() { return inputs; },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  } as Pick<LiveStore, "inputs" | "subscribe">;
  return {
    store,
    set(id: string, state: InputInfo["state"], turnId: string | null) {
      inputs = { ...inputs, [id]: { id, state, turn_id: turnId } };
      listeners.forEach((listener) => listener());
    },
  };
}

async function render(api: FakeApi, running: boolean, sendCommand: Send, inputs = fakeInputStore()) {
  // The extra store prop lets this test fail behaviorally on the old provider, whose props ignored input lifecycle.
  const props = {
    api: api as unknown as EngineApi,
    sessionId: "s1",
    running,
    sendCommand,
    store: inputs.store,
  } as unknown as Parameters<typeof VoiceModeProvider>[0];
  await act(async () => {
    root.render(createElement(VoiceModeProvider, props, createElement(Probe)));
  });
  await settle();
  return inputs;
}

async function toggle() {
  await act(async () => latest.toggle());
  await settle();
}

describe("voice mode toggle through props", () => {
  it("toggle on calls sendCommand with '/voice on' and does not call the thread API", async () => {
    const api = fakeApi({ voice_requested: false });
    const sendCommand = vi.fn();
    await render(api, false, sendCommand);

    await toggle();

    expect(sendCommand.mock.calls[0]?.[0]).toBe("/voice on");
    expect(sendCommand.mock.calls[0]?.[1]).toMatch(/^web-[0-9a-f]{24}$/);
    expect(thread.append).not.toHaveBeenCalled();
  });

  it("toggle does nothing while running or while a command is pending", async () => {
    const api = fakeApi({ voice_requested: false });
    const sendCommand = vi.fn();
    await render(api, true, sendCommand);

    await toggle();
    expect(sendCommand).not.toHaveBeenCalled();
    expect(thread.append).not.toHaveBeenCalled();

    await render(api, false, sendCommand);
    await toggle();
    expect(sendCommand.mock.calls[0]?.[0]).toBe("/voice on");
    expect(sendCommand.mock.calls[0]?.[1]).toMatch(/^web-[0-9a-f]{24}$/);
    expect(latest.pending).toBe(true);

    await toggle();
    expect(sendCommand.mock.calls[0]?.[0]).toBe("/voice on");
    expect(sendCommand.mock.calls[0]?.[1]).toMatch(/^web-[0-9a-f]{24}$/);
  });

  it("clears pending and reports an error when the voice command is rejected", async () => {
    const api = fakeApi({ voice_requested: false });
    const sendCommand = vi.fn().mockRejectedValue(new Error("offline"));
    await render(api, false, sendCommand);

    await toggle();

    expect(latest.pending).toBe(false);
    expect(latest.error).toBe("Could not change voice mode. Try again.");
    expect(container.querySelector('[role="alert"]')?.textContent)
      .toBe("Could not change voice mode. Try again.");
  });

  it("after the run ends, on is set from voice_requested and getHistory is never called", async () => {
    const api = fakeApi({ voice_requested: false });
    const sendCommand = vi.fn();
    const inputs = await render(api, false, sendCommand);
    await toggle();
    const inputId = sendCommand.mock.calls[0]?.[1] as string;
    expect(sendCommand.mock.calls[0]?.[0]).toBe("/voice on");
    expect(inputId).toMatch(/^web-[0-9a-f]{24}$/);

    api.system.voice_requested = true;
    await act(async () => {
      inputs.set(inputId, "started", "turn-1");
      inputs.set(inputId, "ended", "turn-1");
    });
    await settle();

    expect(latest.on).toBe(true);
    expect(latest.pending).toBe(false);
    expect(api.getHistory).not.toHaveBeenCalled();
  });

  it("when voice_requested is undefined, on is unchanged and pending clears", async () => {
    // A success reply in history: the old fallback would have turned the mic on from it.
    const api = fakeApi({});
    api.getHistory.mockResolvedValue({
      history: [{ id: "reply", metadata: { slash_command: "/voice on", command_success: true } }],
    });
    const sendCommand = vi.fn();
    const inputs = await render(api, false, sendCommand);
    await toggle();
    const inputId = sendCommand.mock.calls[0]?.[1] as string;
    expect(sendCommand.mock.calls[0]?.[0]).toBe("/voice on");
    expect(inputId).toMatch(/^web-[0-9a-f]{24}$/);
    expect(latest.pending).toBe(true);

    await act(async () => {
      inputs.set(inputId, "started", "turn-1");
      inputs.set(inputId, "ended", "turn-1");
    });
    await settle();

    expect(latest.on).toBe(false);
    expect(latest.pending).toBe(false);
    expect(api.getHistory).not.toHaveBeenCalled();
  });

  it("confirms a voice command when its input starts and ends before React observes running", async () => {
    const api = fakeApi({ voice_requested: false });
    const sendCommand = vi.fn();
    const inputs = await render(api, false, sendCommand);

    await toggle();
    const inputId = sendCommand.mock.calls[0]?.[1] as string;
    api.system.voice_requested = true;

    // The store notifies synchronously for both stream events; React only gets one outer act/render.
    await act(async () => {
      inputs.set(inputId, "started", "turn-1");
      inputs.set(inputId, "ended", "turn-1");
    });
    await settle();

    expect(latest.pending).toBe(false);
    expect(latest.on).toBe(true);
    expect(api.getSessionState).toHaveBeenCalled();
  });
});
