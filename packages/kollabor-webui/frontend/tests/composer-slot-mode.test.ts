import { expect, test } from "vitest";

import { composerSlotMode } from "../src/components/assistant-ui/thread";
import type { VoiceMode } from "../src/voice-mode";

// The composer's trailing slot reads the running state from the chat store (#1451).
// Its composer part (is the composer empty?) stays on assistant-ui.

const voice = (over: Partial<VoiceMode> = {}): VoiceMode => ({
  pending: false,
  on: false,
  toggle: () => {},
  activity: null,
  ...over,
});

test("text in the composer shows send, whatever runs", () => {
  expect(composerSlotMode(false, true, voice({ on: true }), true)).toBe("send");
});

test("no voice offered: the slot is send", () => {
  expect(composerSlotMode(true, true, null)).toBe("send");
});

test("an empty composer while the store runs a turn shows stop", () => {
  expect(composerSlotMode(true, true, voice())).toBe("stop");
});

test("an empty composer while a permission waits shows stop", () => {
  expect(composerSlotMode(true, false, voice(), true)).toBe("stop");
});

test("an idle empty composer shows the mic, or the listening button while voice is on", () => {
  expect(composerSlotMode(true, false, voice())).toBe("mic");
  expect(composerSlotMode(true, false, voice({ on: true }))).toBe("listening");
});

test("a voice command in flight holds the mic still", () => {
  expect(composerSlotMode(true, true, voice({ pending: true, on: false }))).toBe("mic");
  expect(composerSlotMode(true, true, voice({ pending: true, on: true }))).toBe("listening");
});
