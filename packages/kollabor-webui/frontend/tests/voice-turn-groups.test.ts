import {
  type ThreadMessageLike,
  unstable_createMessageConverter as createMessageConverter,
} from "@assistant-ui/react";
import { describe, expect, it } from "vitest";
import type { HistoryMessage } from "../src/api";
import { buildInitialState } from "../src/runtime";

// The runtime's own converter: assistant-ui joins adjacent assistant messages here.
const converter = createMessageConverter<ThreadMessageLike>((message) => message);

const shown = (history: HistoryMessage[]) =>
  converter.toThreadMessages(buildInitialState("s", [], history).messages, false, {}).map((message) => [
    message.role,
    message.content.map((part) => (part.type === "text" ? part.text : part.type)).join(" | "),
  ]);

const step = { tool_calls: [{ id: "t1", function: { name: "shell", arguments: "{}" } }] };

describe("voice turns in the chat", () => {
  it("a voice greeting after a reply shows as a message of its own", () => {
    expect(shown([
      { role: "user", content: "Stop, that is enough." },
      { role: "assistant", content: "pong: Stop, that is enough.", metadata: { voice_turn_id: "v1" } },
      { role: "assistant", content: "Hi, I'm here.", metadata: { voice_turn_id: "v2" } },
    ])).toEqual([
      ["user", "Stop, that is enough."],
      ["assistant", "pong: Stop, that is enough."],
      ["assistant", "Hi, I'm here."],
    ]);
  });

  it("a typed reply and the voice greeting after it show apart", () => {
    expect(shown([
      { role: "user", content: "hello" },
      { role: "assistant", content: "Hello there." },
      { role: "assistant", content: "Hi, I'm here.", metadata: { voice_turn_id: "v2" } },
    ])).toEqual([
      ["user", "hello"],
      ["assistant", "Hello there."],
      ["assistant", "Hi, I'm here."],
    ]);
  });

  it("a talked-over greeting stays apart from the reply before it", () => {
    expect(shown([
      { role: "user", content: "hello" },
      { role: "assistant", content: "Hello there.", metadata: { voice_turn_id: "v1" } },
      { role: "assistant", content: "Hi, I'm", metadata: { interrupted: true, turn_id: "v2", source: "voice" } },
    ])).toEqual([
      ["user", "hello"],
      ["assistant", "Hello there."],
      ["assistant", "Hi, I'm"],
    ]);
  });

  it("a status-only prompt between them does not join the greeting to the reply", () => {
    expect(shown([
      { role: "user", content: "hello" },
      { role: "assistant", content: "Hello there.", metadata: { voice_turn_id: "v1" } },
      { role: "user", content: "<agent_hud>\n[vault]\nrebirth: 3 sessions\n</agent_hud>" },
      { role: "assistant", content: "Hi, I'm here.", metadata: { voice_turn_id: "v2" } },
    ])).toEqual([
      ["user", "hello"],
      ["assistant", "Hello there."],
      ["assistant", "Hi, I'm here."],
    ]);
  });

  it("a voice turn's tool step and its reply stay one message", () => {
    expect(shown([
      { role: "user", content: "Is the build green?" },
      { role: "assistant", content: "Checking now.", metadata: { voice_turn_id: "v1", ...step } },
      { role: "tool", content: "ok", metadata: { tool_call_id: "t1" } },
      { role: "assistant", content: "It is green.", metadata: { voice_turn_id: "v1" } },
    ])).toEqual([
      ["user", "Is the build green?"],
      ["assistant", "Checking now. | tool-call | It is green."],
    ]);
  });
});
