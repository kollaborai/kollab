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

  it("waits for the injected hub note after the injection offset", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("  const waitForStreamRowKind ="), source.indexOf("  const waitForPromptStatusUpdate ="));
    const note = (text: string) => ({ kind: "stream", ev: "row.upsert", rowKind: "note", parts: [{ type: "hub-note", from: "recorder", text }] });
    const entries = [{ ...note("injected"), row: "old" }, note("unrelated"), { ...note("injected"), row: "new" }];
    const window = { __rec: { net: entries } };
    const wait = runInNewContext(`${body}; waitForStreamRowKind`, {
      Date, window, document: { querySelector: () => ({}) },
      page: { evaluate: async (fn: Function, arg: unknown) => fn(arg) }, sleep: async () => {},
    });
    expect(await wait("note", { afterIndex: 1, hubNote: { from: "recorder", text: "injected" } })).toEqual(entries[2]);
  });

  it("sends the hub note to the selected session presence", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf('    async "hub-note"() {'), source.indexOf("\n  };", source.indexOf('    async "hub-note"() {')));
    let sent: any;
    const flow = runInNewContext(`({${body}})["hub-note"]`, {
      send: async () => {}, waitForRunning: async () => {}, sleep: async () => {},
      page: { evaluate: async (fn: Function) => fn() },
      document: { querySelector: () => ({}) }, window: { __rec: { net: [] } },
      apiJson: async (route: string) => route.startsWith("/sessions/")
        ? { identity: "same", daemon_pid: 42, workspace: "/chosen" }
        : { agents: [{ identity: "same", pid: 41, project: "/other", agent_id: "wrong" }, { identity: "same", pid: 42, project: "/chosen", agent_id: "right" }] },
      postFromClient: async (_route: string, payload: unknown) => { sent = payload; },
      waitForStreamRowKind: async () => {}, settle: async () => {},
      agent: "same", session: "chosen-session", LABEL: "test", tag: "tag", U: () => "u", R: () => "r", tools: () => "tools", say: () => "say",
    });
    await flow();
    expect(sent.agent_id).toBe("right");
  });

  it("fails the hub flow when an existing user row changes", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf('    async "hub-note"() {'), source.indexOf("\n  };", source.indexOf('    async "hub-note"() {')));
    const row = { textContent: "original" };
    const window = { __rec: { net: [], snapshotUserRows: () => [{ id: 1, text: row.textContent }] } };
    const flow = runInNewContext(`({${body}})["hub-note"]`, {
      send: async () => {}, waitForRunning: async () => {}, sleep: async () => {},
      page: { evaluate: async (fn: Function, arg: unknown) => fn(arg) },
      document: { querySelector: () => ({}) }, window,
      apiJson: async (route: string) => route.startsWith("/sessions/")
        ? { identity: "same", daemon_pid: 42 } : { agents: [{ identity: "same", pid: 42, agent_id: "right" }] },
      postFromClient: async () => { row.textContent = "mutated"; },
      waitForStreamRowKind: async () => {}, settle: async () => {},
      agent: "same", session: "session", LABEL: "test", tag: "tag", U: () => "u", R: () => "r", tools: () => "tools", say: () => "say",
    });
    await expect(flow()).rejects.toThrow(/user row/i);
  });

  it("reports ACP goal reload as unsupported without sending a chat prompt", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const start = source.indexOf('    async "goal-reload"() {');
    const body = source.slice(start, source.indexOf("    // Inject a real hub", start));
    let sent = false;
    const flow = runInNewContext(`({${body}})["goal-reload"]`, {
      activeRuntime: "acp", send: async () => { sent = true; }, command: async () => {},
      reloadDuringRun: async () => {}, U: () => "u", R: () => "r", tools: () => "tools", say: () => "say",
    });
    await expect(flow()).rejects.toThrow(/unsupported.*ACP|ACP.*unsupported/i);
    expect(sent).toBe(false);
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

  it("requires a stream replacement for successful ACP compaction", () => {
    expect(isCompactionComplete({
      sawStart: true,
      status: "idle",
      latestEventStatus: "idle",
      replaced: false,
      runtime: "codex-cli",
    })).toBe(false);
    expect(isCompactionComplete({
      sawStart: true, status: "idle", latestEventStatus: "idle",
      replaced: true, runtime: "codex-cli",
    })).toBe(true);
  });

  it("fails an idle ACP compaction when no post-command replacement arrives", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("  const waitForCompaction ="), source.indexOf("  const runtimeForSession ="));
    let now = 0;
    let polls = 0;
    const wait = runInNewContext(`${body}; waitForCompaction`, {
      Date: { now: () => now }, isCompactionComplete, activeRuntime: "codex-cli",
      contextCompactionStatus: async () => ++polls === 1 ? "compacting" : "idle",
      page: { evaluate: async () => [{ kind: "events", ev: "context_compaction_status", status: "compacting" }] },
      sleep: async () => { now += 100000; },
    });
    await expect(wait(0)).rejects.toThrow(/compaction did not finish/);
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

  it("waits for an idle state observed after the compaction start event", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("  const waitForCompaction ="), source.indexOf("  const runtimeForSession ="));
    let polls = 0;
    const wait = runInNewContext(`${body}; waitForCompaction`, {
      Date, isCompactionComplete, activeRuntime: "codex-cli",
      contextCompactionStatus: async () => { polls++; return polls === 1 ? "idle" : polls === 2 ? "compacting" : "idle"; },
      page: { evaluate: async () => [{ kind: "events", ev: "context_compaction_status", status: "compacting" }, ...(polls >= 3 ? [{ kind: "stream", ev: "replace" }] : [])] },
      sleep: async () => {},
    });
    await wait(0);
    expect(polls).toBe(3);
  });

  it("requires a fresh idle after a new start even if an idle event follows it", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("  const waitForCompaction ="), source.indexOf("  const runtimeForSession ="));
    let polls = 0;
    const event = (status: string) => ({ kind: "events", ev: "context_compaction_status", status });
    const wait = runInNewContext(`${body}; waitForCompaction`, {
      Date, isCompactionComplete, activeRuntime: "codex-cli",
      contextCompactionStatus: async () => { polls++; return polls === 1 ? "compacting" : "idle"; },
      page: { evaluate: async () => polls === 1 ? [] : [event("compacting"), event("idle"), ...(polls >= 3 ? [{ kind: "stream", ev: "replace" }] : [])] },
      sleep: async () => {},
    });
    await wait(0);
    expect(polls).toBe(3);
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

  it("counts a tool call once across row IDs and reload", () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("function installRecorder(cfg)"), source.indexOf("// ---------------------------------------------------------------- one flow"));
    const installPage = (saved: string | null) => {
      const window = { fetch: async () => new Response() };
      const install = runInNewContext(`(${body})`, {
        window, Request, Response, ReadableStream, TextDecoder, URL, performance,
        location: { href: "http://localhost/" },
        sessionStorage: { getItem: () => saved, removeItem: () => {} },
        requestAnimationFrame: () => {},
      });
      install({ wall0: Date.now(), sessionStreamSuffix: "/sessions/target/stream", user: [], reply: [] });
      return (window as any).__rec;
    };
    const row = (id: string) => ({ id, seq: 10, kind: "tool", parts: [{ type: "tool-call", tool_call_id: "call-1" }] });
    const rec = installPage(null);
    rec.flowSeq = 1;
    rec.noteToolRow(row("row-1"));
    rec.noteToolRow(row("row-2"));
    expect(rec.toolsStarted).toBe(1);
    const restored = installPage(JSON.stringify(rec));
    restored.noteToolRow(row("row-3"));
    expect(restored.toolsStarted).toBe(1);
  });

  it("associates a previously turnless call with its measured turn on recovery", () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("function installRecorder(cfg)"), source.indexOf("// ---------------------------------------------------------------- one flow"));
    const window = { fetch: async () => new Response() };
    const install = runInNewContext(`(${body})`, {
      window, Request, Response, ReadableStream, TextDecoder, URL, performance,
      location: { href: "http://localhost/" },
      sessionStorage: { getItem: () => null, removeItem: () => {} }, requestAnimationFrame: () => {},
    });
    install({ wall0: Date.now(), sessionStreamSuffix: "/sessions/target/stream", user: ["marker"], reply: [] });
    const rec = (window as any).__rec;
    rec.mark("PAGE SEND U1");
    rec.noteToolRow({ kind: "input", id: "u", turn_id: "t", parts: [{ type: "text", text: "marker" }] });
    const row = (turn_id: string | null) => ({ kind: "tool", turn_id, parts: [{ type: "tool-call", tool_call_id: "c" }] });
    rec.noteToolRow(row(null));
    expect(rec.toolsStarted).toBe(0);
    rec.noteToolRow(row("t"));
    expect(rec.toolsStarted).toBe(1);
    expect(analyzeChatFrames([frame({ toolsDrawn: 1, toolsStarted: rec.toolsStarted })], ready).defects.dupTools ?? 0).toBe(0);
  });

  it("counts tool-call parts on every row kind", () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("function installRecorder(cfg)"), source.indexOf("// ---------------------------------------------------------------- one flow"));
    const window = { fetch: async () => new Response() };
    const install = runInNewContext(`(${body})`, {
      window, Request, Response, ReadableStream, TextDecoder, URL, performance,
      location: { href: "http://localhost/" },
      sessionStorage: { getItem: () => null, removeItem: () => {} },
      requestAnimationFrame: () => {},
    });
    install({ wall0: Date.now(), sessionStreamSuffix: "/sessions/target/stream", user: [], reply: [] });
    const rec = (window as any).__rec;
    rec.flowSeq = 1;
    for (const kind of ["input", "reply", "tool", "note", "prompt", "marker"]) {
      rec.noteToolRow({ kind, parts: [{ type: "tool-call", tool_call_id: kind }] });
    }
    expect(rec.toolsStarted).toBe(6);
    expect(analyzeChatFrames([frame({ toolsDrawn: 6, toolsStarted: rec.toolsStarted })], ready).defects.dupTools ?? 0).toBe(0);
  });

  it("excludes seed calls from the measured U1 flow across recovery", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("function installRecorder(cfg)"), source.indexOf("// ---------------------------------------------------------------- one flow"));
    let event: any;
    const window = { fetch: async () => new Response(`data: ${JSON.stringify(event)}\n\n`) };
    const install = runInNewContext(`(${body})`, {
      window, Request, Response, ReadableStream, TextDecoder, URL, performance,
      location: { href: "http://localhost/" },
      sessionStorage: { getItem: () => null, removeItem: () => {} },
      requestAnimationFrame: () => {},
    });
    install({ wall0: Date.now(), sessionStreamSuffix: "/sessions/target/stream", user: ["U1-marker"], reply: [] });
    const rec = (window as any).__rec;
    const row = (call: string) => ({ kind: "tool", turn_id: call === "seed" ? "old" : "measured-turn", parts: [{ type: "tool-call", tool_call_id: call }] });
    const emit = async (type: string, data: any, seq: number) => {
      event = { type, data, seq };
      await (await (window.fetch as any)("http://localhost/sessions/target/stream")).text();
    };
    rec.flowSeq = 1;
    await emit("row.upsert", row("seed"), 1);
    rec.mark("PAGE SEND U1");
    await emit("snapshot", { rows: [row("seed"), { id: "u1", kind: "input", turn_id: "measured-turn", parts: [{ type: "text", text: "U1-marker" }] }, row("measured")], next_seq: 3 }, 2);
    await emit("replace", { rows: [row("seed"), row("measured")] }, 3);
    expect(rec.toolsStarted).toBe(1);
    expect(analyzeChatFrames([frame({ toolsDrawn: 2, toolsStarted: rec.toolsStarted })], ready).defects.dupTools).toBe(1);
  });

  it("excludes unseen old-turn calls recovered after U1 and counts measured concurrent turns", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("function installRecorder(cfg)"), source.indexOf("// ---------------------------------------------------------------- one flow"));
    let event: any;
    const window = { fetch: async () => new Response(`data: ${JSON.stringify(event)}\n\n`) };
    const install = runInNewContext(`(${body})`, {
      window, Request, Response, ReadableStream, TextDecoder, URL, performance,
      location: { href: "http://localhost/" },
      sessionStorage: { getItem: () => null, removeItem: () => {} },
      requestAnimationFrame: () => {},
    });
    install({ wall0: Date.now(), sessionStreamSuffix: "/sessions/target/stream", user: ["U1-marker", "U2-marker"], reply: [] });
    const rec = (window as any).__rec;
    const emit = async (type: string, data: any, seq: number) => {
      event = { type, data, seq };
      await (await (window.fetch as any)("http://localhost/sessions/target/stream")).text();
    };
    const tool = (call: string, turn_id: string) => ({ kind: "tool", turn_id, parts: [{ type: "tool-call", tool_call_id: call }] });
    const input = (id: string, marker: string) => ({ id, kind: "input", turn_id: null, parts: [{ type: "text", text: marker }] });
    rec.flowSeq = 1;
    rec.mark("PAGE SEND U1");
    await emit("snapshot", { rows: [tool("old-snapshot", "old"), input("u1", "U1-marker"), tool("current", "turn-1")], turns: [], inputs: [], next_seq: 3 }, 2);
    await emit("replace", { rows: [tool("old-replace", "old"), tool("current", "turn-1"), tool("second", "turn-2"), input("u2", "U2-marker")] }, 3);
    await emit("turn.state", { turn_id: "turn-2", state: "running", input_ids: ["u2"] }, 4);
    await emit("input.state", { id: "u1", state: "joined", turn_id: "turn-1" }, 5);
    expect(rec.toolsStarted).toBe(2);
    expect(analyzeChatFrames([frame({ toolsDrawn: 3, toolsStarted: rec.toolsStarted })], ready).defects.dupTools).toBe(1);
  });

  it("counts newly observed calls independently of row sequences through recovery", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("function installRecorder(cfg)"), source.indexOf("// ---------------------------------------------------------------- one flow"));
    let event: any;
    const window = { fetch: async () => new Response(`data: ${JSON.stringify(event)}\n\n`) };
    const install = runInNewContext(`(${body})`, {
      window, Request, Response, ReadableStream, TextDecoder, URL, performance,
      location: { href: "http://localhost/" },
      sessionStorage: { getItem: () => null, removeItem: () => {} },
      requestAnimationFrame: () => {},
    });
    install({ wall0: Date.now(), sessionStreamSuffix: "/sessions/target/stream", user: [], reply: [] });
    const rec = (window as any).__rec;
    const row = (id: string, call: string, seq: number) => ({ id, seq, kind: "tool", parts: [{ type: "tool-call", tool_call_id: call }] });
    // Use the actual intercepted session URL for every stream event.
    const send = async (type: string, data: any, seq: number) => {
      event = { type, data, seq };
      await (await (window.fetch as any)("http://localhost/sessions/target/stream")).text();
    };
    await send("snapshot", { rows: [row("old", "old-call", 3)], next_seq: 41 }, 1);
    rec.flowSeq = rec.lastStreamSeq + 1;
    await send("row.upsert", row("new", "new-call", 6), 41);
    await send("snapshot", { rows: [row("old-again", "old-call", 3), row("recovered", "recovered-call", 7)], next_seq: 44 }, 43);
    await send("replace", { rows: [row("replacement", "replacement-call", 8), row("duplicate", "recovered-call", 7)] }, 44);
    expect(rec.toolsStarted).toBe(3);
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
