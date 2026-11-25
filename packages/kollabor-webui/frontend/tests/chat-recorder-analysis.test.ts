import { describe, expect, it } from "vitest";
import {
  analyzeChatFrames,
  CHAT_RECORDER_FLOWS,
  classifyChatRuntime,
  chatRecorderStreamSuffix,
  findPromptStatusUpdate,
  hasRunningTurnWithInputs,
  isCompactionComplete,
  parseRuntimeMatrix,
} from "../scripts/chat-recorder-analysis.mjs";

const ready = [{ t: 0, name: "READY" }];

function frame(overrides: Record<string, unknown> = {}) {
  return {
    t: 0,
    n: 3,
    rows: ["u1:abc:1", "a2:def:1", "u3:ghi:1"],
    users: [0, 0, 0],
    replies: [0, 0, 0],
    fading: 0,
    toolsDrawn: 0,
    toolsStarted: 0,
    st: 0,
    pick: 0,
    err: 0,
    errText: "",
    run: 0,
    ...overrides,
  };
}

describe("chat recorder analysis", () => {
  it("requires a later upsert for the same prompt row with a changed status", () => {
    const events = [
      { kind: "stream", ev: "row.upsert", row: "prompt-1", status: "pending" },
      { kind: "stream", ev: "row.upsert", row: "prompt-2", status: "approved" },
      { kind: "stream", ev: "row.upsert", row: "prompt-1", status: "approved" },
    ];

    expect(findPromptStatusUpdate(events, "prompt-1", "pending", 1)).toEqual(events[2]);
    expect(findPromptStatusUpdate(events, "prompt-1", "pending", 2)).toBeNull();
  });

  it("requires the reloaded snapshot to retain the active turn and its running inputs", () => {
    const expectedTurn = { turn_id: "turn-1", input_ids: ["input-1"] };
    expect(hasRunningTurnWithInputs({
      turns: [{ turn_id: "turn-1", state: "running", input_ids: ["input-1"] }],
      inputs: [{ id: "input-1", turn_id: "turn-1", state: "started" }],
    }, expectedTurn)).toBe(true);
    expect(hasRunningTurnWithInputs({
      turns: [{ turn_id: "turn-1", state: "running", input_ids: ["input-1"] }],
      inputs: [],
    }, expectedTurn)).toBe(false);
    expect(hasRunningTurnWithInputs({
      turns: [{ turn_id: "turn-1", state: "ended", input_ids: ["input-1"] }],
      inputs: [{ id: "input-1", turn_id: "turn-1", state: "ended" }],
    }, expectedTurn)).toBe(false);
  });

  it("does not accept an idle compaction before the stream replacement arrives", () => {
    expect(isCompactionComplete({
      sawStart: true,
      status: "idle",
      latestEventStatus: "idle",
      replaced: false,
      runtime: "native",
    })).toBe(false);
    expect(isCompactionComplete({
      sawStart: true,
      status: "idle",
      latestEventStatus: "idle",
      replaced: true,
      runtime: "native",
    })).toBe(true);
  });

  it("accepts successful ACP compaction without a transcript replacement", () => {
    expect(isCompactionComplete({
      sawStart: true,
      status: "idle",
      latestEventStatus: "idle",
      replaced: false,
      runtime: "codex-cli",
    })).toBe(true);
  });

  it("lets a staged native compaction reach the request that applies it", () => {
    expect(isCompactionComplete({
      sawStart: true,
      status: "ready_to_retry",
      latestEventStatus: "ready_to_retry",
      replaced: false,
      runtime: "native",
    })).toBe(true);
    expect(isCompactionComplete({
      sawStart: true,
      status: "applied",
      latestEventStatus: "applied",
      replaced: false,
      runtime: "native",
      phase: "transcript",
    })).toBe(false);
    expect(isCompactionComplete({
      sawStart: true,
      status: "applied",
      latestEventStatus: "applied",
      replaced: true,
      runtime: "native",
      phase: "transcript",
    })).toBe(true);
  });

  it("keeps the existing defect counters and detects their frame signatures", () => {
    const frames = [
      frame({ t: 0, anc: 100 }),
      frame({
        t: 100,
        rows: ["u4:abc:1", "a5:def:1", "u6:ghi:1"],
        users: [2, 0, 0],
        fading: 3,
        pick: 1,
        err: 1,
        errText: "Stream ended abruptly",
        anc: 120,
      }),
      frame({ t: 200, anc: null, err: 1 }),
      frame({ t: 300, n: 0, rows: [], err: 1 }),
    ];

    const result = analyzeChatFrames(frames, ready);

    expect(result.defects).toMatchObject({
      blank: 1,
      remount: 1,
      massFade: 1,
      dupUser: 1,
      picker: 1,
      anchorMoved: 1,
      anchorLost: 1,
      errorBoxLeft: 1,
    });
    expect(result.events.map((event: { name: string }) => event.name)).toContain("ERROR-BOX");
  });

  it("keeps reply and tool duplicate counters", () => {
    const result = analyzeChatFrames(
      [
        frame({ t: 0 }),
        frame({ t: 100, replies: [2, 0, 0], toolsDrawn: 2, toolsStarted: 1 }),
        frame({ t: 200, replies: [0, 0, 0] }),
      ],
      ready,
    );

    expect(result.defects).toMatchObject({ dupReply: 1, dupTools: 1, replyGone: 1 });
  });

  it("lists the requested recorder flows", () => {
    expect(CHAT_RECORDER_FLOWS).toEqual(
      expect.arrayContaining(["prompt-other", "compact", "goal-reload", "hub-note"]),
    );
  });

  it("parses one explicit agent for every runtime in the matrix", () => {
    expect(
      parseRuntimeMatrix("native=local-agent,codex-cli=codex-agent,claude-code=claude-agent"),
    ).toEqual({
      native: "local-agent",
      "codex-cli": "codex-agent",
      "claude-code": "claude-agent",
    });
  });

  it("rejects incomplete, duplicate, unknown, and empty runtime entries", () => {
    for (const value of [
      "native=local-agent,codex-cli=codex-agent",
      "native=first,native=second,codex-cli=codex-agent,claude-code=claude-agent",
      "native=local-agent,codex-cli=codex-agent,claude=claude-agent",
      "native=local-agent,codex-cli=,claude-code=claude-agent",
    ]) {
      expect(() => parseRuntimeMatrix(value)).toThrow();
    }
  });

  it("classifies native and ACP sessions from engine session metadata", () => {
    expect(classifyChatRuntime({ provider: "anthropic" })).toBe("native");
    expect(classifyChatRuntime({ provider: "acp", harness: "OpenAI Codex CLI" })).toBe("codex-cli");
    expect(classifyChatRuntime({ provider: "ACP", profile: "claude-code" })).toBe("claude-code");
    expect(classifyChatRuntime({ provider: "acp", harness: "unknown harness" })).toBe("acp");
  });

  it("builds a stream suffix scoped to the session under test", () => {
    const suffix = chatRecorderStreamSuffix("session one");

    expect(`/sessions/${encodeURIComponent("session one")}/stream`.endsWith(suffix)).toBe(true);
    expect("/sessions/another-session/stream".endsWith(suffix)).toBe(false);
    expect(`/sessions/${encodeURIComponent("session one")}/inputs`.endsWith(suffix)).toBe(false);
  });
});
