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
const restoredIds = (history: HistoryMessage[]) =>
  buildInitialState("s", [], history).messages.map((message) => message.id);
const restoredMessages = (history: HistoryMessage[]) =>
  buildInitialState("s", [], history).messages;

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

  it("restores forced ACP turns in execution order without duplicating rows", () => {
    const acpUser = (
      requestId: string,
      turnId: number,
      content: string,
      dispatchOrder: number,
      forceSend = false,
    ): HistoryMessage => ({
      role: "user",
      content,
      metadata: {
        provider: "acp",
        request_id: requestId,
        turn_id: turnId,
        acp_turn_key: `turn-${turnId}`,
        dispatch_epoch: "epoch-1",
        dispatch_order: dispatchOrder,
        queue_handoff: { force_send: forceSend },
      },
    });
    const acpReply = (
      requestId: string,
      turnId: number,
      content: string,
      dispatchOrder: number,
    ): HistoryMessage => ({
      role: "assistant",
      content,
      metadata: {
        provider: "acp",
        request_id: requestId,
        turn_id: turnId,
        acp_turn_key: `turn-${turnId}`,
        dispatch_epoch: "epoch-1",
        dispatch_order: dispatchOrder,
      },
    });

    // E was admitted first, then Send Now moved the B/C batch ahead of it.
    // JSONL keeps input chronology, while the UI must pair each prompt with
    // its reply and display completed turns in the actual execution order.
    const history = [
      acpUser("E", 1, "older E", 3),
      acpUser("B", 2, "forced B", 1, true),
      acpUser("C", 3, "forced C", 2, true),
      acpReply("B", 2, "reply B", 1),
      acpReply("C", 3, "reply C", 2),
      acpReply("E", 1, "reply E", 3),
    ];

    expect(shown(history)).toEqual([
      ["user", "forced B"],
      ["assistant", "reply B"],
      ["user", "forced C"],
      ["assistant", "reply C"],
      ["user", "older E"],
      ["assistant", "reply E"],
    ]);
    expect(history).toHaveLength(6);
    expect(restoredIds(history)).toEqual([
      "history-1",
      "history-3",
      "history-2",
      "history-4",
      "history-0",
      "history-5",
    ]);
  });

  it("does not infer execution order when one ACP turn has no dispatch marker", () => {
    const history: HistoryMessage[] = [
      {
        role: "user", content: "older E",
        metadata: { provider: "acp", request_id: "E", turn_id: 1, acp_turn_key: "key-E",
          dispatch_epoch: "epoch-1", dispatch_order: 3 },
      },
      {
        role: "user", content: "forced B",
        metadata: { provider: "acp", request_id: "B", turn_id: 2, acp_turn_key: "key-B",
          queue_handoff: { force_send: true } },
      },
      {
        role: "assistant", content: "reply B",
        metadata: { provider: "acp", request_id: "B", turn_id: 2, acp_turn_key: "key-B",
          dispatch_epoch: "epoch-1", dispatch_order: 1 },
      },
      {
        role: "assistant", content: "reply E",
        metadata: { provider: "acp", request_id: "E", turn_id: 1, acp_turn_key: "key-E",
          dispatch_epoch: "epoch-1", dispatch_order: 3 },
      },
    ];

    expect(shown(history)).toEqual([
      ["user", "older E"],
      ["user", "forced B"],
      ["assistant", "reply B"],
      ["assistant", "reply E"],
    ]);
    expect(restoredIds(history)).toEqual([
      "history-0", "history-1", "history-2", "history-3",
    ]);
  });

  it("keeps partial cancelled output in its persisted dispatch slot", () => {
    const history: HistoryMessage[] = [
      {
        role: "user", content: "older E",
        metadata: { provider: "acp", request_id: "E", turn_id: 1, acp_turn_key: "key-E",
          dispatch_epoch: "epoch-1", dispatch_order: 3 },
      },
      {
        role: "user", content: "forced B",
        metadata: { provider: "acp", request_id: "B", turn_id: 2, acp_turn_key: "key-B",
          dispatch_epoch: "epoch-1", dispatch_order: 1, queue_handoff: { force_send: true } },
      },
      {
        role: "user", content: "forced C",
        metadata: { provider: "acp", request_id: "C", turn_id: 3, acp_turn_key: "key-C",
          dispatch_epoch: "epoch-1", dispatch_order: 2, queue_handoff: { force_send: true } },
      },
      {
        role: "assistant", content: "reply B",
        metadata: { provider: "acp", request_id: "B", turn_id: 2, acp_turn_key: "key-B",
          dispatch_epoch: "epoch-1", dispatch_order: 1, stop_reason: "end_turn" },
      },
      {
        role: "assistant", content: "partial C",
        metadata: { provider: "acp", request_id: "C", turn_id: 3, acp_turn_key: "key-C",
          dispatch_epoch: "epoch-1", dispatch_order: 2, stop_reason: "cancelled" },
      },
      {
        role: "assistant", content: "reply E",
        metadata: { provider: "acp", request_id: "E", turn_id: 1, acp_turn_key: "key-E",
          dispatch_epoch: "epoch-1", dispatch_order: 3, stop_reason: "end_turn" },
      },
    ];

    expect(shown(history)).toEqual([
      ["user", "forced B"],
      ["assistant", "reply B"],
      ["user", "forced C"],
      ["assistant", "partial C"],
      ["user", "older E"],
      ["assistant", "reply E"],
    ]);
    const messages = restoredMessages(history);
    expect(messages.map((message) => message.id)).toEqual([
      "history-1", "history-3", "history-2", "history-4", "history-0", "history-5",
    ]);
    expect(messages[3].status).toEqual({ type: "incomplete", reason: "cancelled" });
  });

  it("keeps an admitted but unstarted batch item visible without inventing its dispatch order", () => {
    const history: HistoryMessage[] = [
      {
        role: "user", content: "older E",
        metadata: { provider: "acp", request_id: "E", turn_id: 1, acp_turn_key: "key-E",
          dispatch_epoch: "epoch-1", dispatch_order: 3 },
      },
      {
        role: "user", content: "forced B",
        metadata: { provider: "acp", request_id: "B", turn_id: 2, acp_turn_key: "key-B",
          dispatch_epoch: "epoch-1", dispatch_order: 1, queue_handoff: { force_send: true } },
      },
      {
        role: "user", content: "forced C pending",
        metadata: { provider: "acp", request_id: "C", turn_id: 3, acp_turn_key: "key-C",
          queue_handoff: { force_send: true } },
      },
      {
        role: "assistant", content: "reply B",
        metadata: { provider: "acp", request_id: "B", turn_id: 2, acp_turn_key: "key-B",
          dispatch_epoch: "epoch-1", dispatch_order: 1 },
      },
      {
        role: "assistant", content: "reply E",
        metadata: { provider: "acp", request_id: "E", turn_id: 1, acp_turn_key: "key-E",
          dispatch_epoch: "epoch-1", dispatch_order: 3 },
      },
    ];

    expect(restoredIds(history)).toEqual([
      "history-0", "history-1", "history-2", "history-3", "history-4",
    ]);
    expect(shown(history)).toEqual([
      ["user", "older E"],
      ["user", "forced B"],
      ["user", "forced C pending"],
      ["assistant", "reply B"],
      ["assistant", "reply E"],
    ]);
  });

  it("keeps duplicate, missing, or mismatched turn identities in source order", () => {
    const history: HistoryMessage[] = [
      {
        role: "user", content: "older E",
        metadata: { provider: "acp", request_id: "E", turn_id: 1, acp_turn_key: "key-E",
          dispatch_epoch: "epoch-1", dispatch_order: 3 },
      },
      {
        role: "user", content: "forced B",
        metadata: { provider: "acp", request_id: "B", turn_id: 2, acp_turn_key: "key-B",
          dispatch_epoch: "epoch-1", dispatch_order: 1 },
      },
      {
        role: "user", content: "forced C",
        metadata: { provider: "acp", request_id: "C", turn_id: 3, acp_turn_key: "key-C",
          dispatch_epoch: "epoch-1", dispatch_order: 1 },
      },
      {
        role: "assistant", content: "reply B with wrong key",
        metadata: { provider: "acp", request_id: "B", turn_id: 2, acp_turn_key: "wrong-key",
          dispatch_epoch: "epoch-1", dispatch_order: 1 },
      },
      {
        role: "assistant", content: "reply C",
        metadata: { provider: "acp", request_id: "C", turn_id: 3, acp_turn_key: "key-C",
          dispatch_epoch: "epoch-1", dispatch_order: 1 },
      },
      {
        role: "user", content: "missing ACP turn key",
        metadata: { provider: "acp", request_id: "missing-key", turn_id: 4 },
      },
      {
        role: "assistant", content: "reply E",
        metadata: { provider: "acp", request_id: "E", turn_id: 1, acp_turn_key: "key-E",
          dispatch_epoch: "epoch-1", dispatch_order: 3 },
      },
    ];

    expect(restoredIds(history)).toEqual([
      "history-0", "history-1", "history-2", "history-3", "history-4", "history-5", "history-6",
    ]);
    expect(shown(history).map(([, content]) => content)).toEqual([
      "older E", "forced B", "forced C", "reply B with wrong key", "reply C", "missing ACP turn key", "reply E",
    ]);
  });

  it("does not join rows that reuse the same request and turn key across turn IDs", () => {
    const history: HistoryMessage[] = [
      {
        role: "user", content: "first reused identity",
        metadata: { provider: "acp", request_id: "reused", turn_id: 1, acp_turn_key: "same-key",
          dispatch_epoch: "epoch-1", dispatch_order: 3 },
      },
      {
        role: "user", content: "second reused identity",
        metadata: { provider: "acp", request_id: "reused", turn_id: 2, acp_turn_key: "same-key",
          dispatch_epoch: "epoch-1", dispatch_order: 1 },
      },
      {
        role: "assistant", content: "second reply",
        metadata: { provider: "acp", request_id: "reused", turn_id: 2, acp_turn_key: "same-key",
          dispatch_epoch: "epoch-1", dispatch_order: 1 },
      },
      {
        role: "assistant", content: "first reply",
        metadata: { provider: "acp", request_id: "reused", turn_id: 1, acp_turn_key: "same-key",
          dispatch_epoch: "epoch-1", dispatch_order: 3 },
      },
    ];

    expect(restoredIds(history)).toEqual([
      "history-0", "history-1", "history-2", "history-3",
    ]);
    expect(shown(history).map(([, content]) => content)).toEqual([
      "first reused identity", "second reused identity", "second reply", "first reply",
    ]);
  });

  it("orders ACP turns only inside their source block around native rows", () => {
    const history: HistoryMessage[] = [
      { role: "user", content: "native before" },
      {
        role: "user", content: "older E",
        metadata: { provider: "acp", request_id: "E", turn_id: 1, acp_turn_key: "key-E",
          dispatch_epoch: "epoch-1", dispatch_order: 3 },
      },
      {
        role: "user", content: "forced B",
        metadata: { provider: "acp", request_id: "B", turn_id: 2, acp_turn_key: "key-B",
          dispatch_epoch: "epoch-1", dispatch_order: 1 },
      },
      {
        role: "assistant", content: "reply B",
        metadata: { provider: "acp", request_id: "B", turn_id: 2, acp_turn_key: "key-B",
          dispatch_epoch: "epoch-1", dispatch_order: 1 },
      },
      {
        role: "assistant", content: "reply E",
        metadata: { provider: "acp", request_id: "E", turn_id: 1, acp_turn_key: "key-E",
          dispatch_epoch: "epoch-1", dispatch_order: 3 },
      },
      { role: "user", content: "native after" },
    ];

    expect(shown(history).map(([, content]) => content)).toEqual([
      "native before", "forced B", "reply B", "older E", "reply E", "native after",
    ]);
    expect(restoredIds(history)).toEqual([
      "history-0", "history-2", "history-3", "history-1", "history-4", "history-5",
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
