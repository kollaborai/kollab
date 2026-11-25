import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import {
  analyzeChatFrames,
  CHAT_RECORDER_FLOWS,
  classifyChatRuntime,
  chatRecorderStreamSuffix,
  captureRecorderFailure,
  findPromptStatusUpdate,
  hasRunningTurnWithInputs,
  isCompactionComplete,
  requireRecorderApprovalMode,
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

  it("does not complete ACP compaction from an idle event older than the current start", () => {
    expect(isCompactionComplete({
      sawStart: true,
      status: "compacting",
      latestEventStatus: "idle",
      replaced: false,
      runtime: "codex-cli",
    })).toBe(false);
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
      frame({ t: 200, rows: ["u4:abc:1", "a5:def:1", "u6:ghi:1"], anc: null, err: 1 }),
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

  it("counts every consecutive all-new-row frame as a remount", () => {
    const result = analyzeChatFrames(
      [
        frame({ t: 0 }),
        frame({ t: 100, rows: ["u4:abc:1", "a5:def:1", "u6:ghi:1"] }),
        frame({ t: 200, rows: ["u7:abc:1", "a8:def:1", "u9:ghi:1"] }),
        frame({ t: 300, rows: ["u10:abc:1", "a11:def:1", "u12:ghi:1"] }),
      ],
      ready,
    );

    expect(result.defects.remount).toBe(3);
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

  it("records event streams only for the selected session", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("function installRecorder(cfg)"), source.indexOf("// ---------------------------------------------------------------- one flow"));
    const window = { fetch: async (_url: string) => new Response('data: {"type":"context_compaction_status","status":"idle"}\n\n') };
    const install = runInNewContext(`(${body})`, {
      window, Request, Response, ReadableStream, TextDecoder, URL, performance,
      location: { href: "http://localhost/" },
      sessionStorage: { getItem: () => null, removeItem: () => {} },
      requestAnimationFrame: () => {},
    });
    install({ wall0: Date.now(), sessionStreamSuffix: chatRecorderStreamSuffix("target"), user: [], reply: [] });
    await (await window.fetch("http://localhost/sessions/other/events")).text();
    await (await window.fetch("http://localhost/sessions/target/events")).text();
    const entries = (window as any).__rec.net.filter((entry: any) => entry.ev === "context_compaction_status");
    expect(entries).toHaveLength(1);
  });

  it("requires a known approval mode before the prompt flow changes it", () => {
    expect(requireRecorderApprovalMode("trust_all")).toBe("trust_all");
    expect(requireRecorderApprovalMode("confirm_all")).toBe("confirm_all");
    expect(() => requireRecorderApprovalMode("")).toThrow(
      "the engine does not know this agent's approval mode; open the agent and retry",
    );
    expect(() => requireRecorderApprovalMode("unknown")).toThrow();
  });

  it("records a failure when the expected reply never settles", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("  const settle ="), source.indexOf("  const tools ="));
    const marks: string[] = [];
    const settle = runInNewContext(`${body}; settle`, {
      sleep: async () => {},
      page: { evaluate: async () => ({ run: false, has: false }) },
      mark: async (name: string) => { marks.push(name); },
    });

    const failure = await captureRecorderFailure(() => settle("missing", 0, 1));
    expect(marks).toContain("SETTLE TIMEOUT");
    expect(failure).toMatch(/settle.*timeout/i);
  });

  it("keeps setup failures in the recorder flow result", async () => {
    await expect(captureRecorderFailure(async () => {
      throw new Error("the chat stream did not send its initial snapshot");
    })).resolves.toBe("Error: the chat stream did not send its initial snapshot");
  });
});
