// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EngineApi } from "@/api";
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
  return null;
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

async function render(api: FakeApi, running: boolean, sendCommand: Send) {
  await act(async () => {
    root.render(
      <VoiceModeProvider api={api as unknown as EngineApi} sessionId="s1" running={running} sendCommand={sendCommand}>
        <Probe />
      </VoiceModeProvider>,
    );
  });
  await settle();
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

    expect(sendCommand.mock.calls).toEqual([["/voice on"]]);
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
    expect(sendCommand.mock.calls).toEqual([["/voice on"]]);
    expect(latest.pending).toBe(true);

    await toggle();
    expect(sendCommand.mock.calls).toEqual([["/voice on"]]);
  });

  it("after the run ends, on is set from voice_requested and getHistory is never called", async () => {
    const api = fakeApi({ voice_requested: false });
    const sendCommand = vi.fn();
    await render(api, false, sendCommand);
    await toggle();
    expect(sendCommand.mock.calls).toEqual([["/voice on"]]);

    api.system.voice_requested = true;
    await render(api, true, sendCommand);
    await render(api, false, sendCommand);

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
    await render(api, false, sendCommand);
    await toggle();
    expect(sendCommand.mock.calls).toEqual([["/voice on"]]);
    expect(latest.pending).toBe(true);

    await render(api, true, sendCommand);
    await render(api, false, sendCommand);

    expect(latest.on).toBe(false);
    expect(latest.pending).toBe(false);
    expect(api.getHistory).not.toHaveBeenCalled();
  });
});
