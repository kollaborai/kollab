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
  hasMatchingGoalTurn,
  isCompactionComplete,
  requireRecorderApprovalMode,
  parseRuntimeMatrix,
} from "../scripts/chat-recorder-analysis.mjs";

const ready = [{ t: 0, name: "READY" }];
const privateGoalReplyId = "recorder-private-goal-capability";

function privateGoalPage(shortGoalId: string, conversationUid: string) {
  let legacyReceiptRead = false;
  let replyIdRead = false;
  return {
    evaluate: async (_fn: unknown, marker?: unknown) => {
      // Keep old-flow fixtures useful while the implementation moves to polling.
      if (typeof marker === "string") {
        legacyReceiptRead = true;
        return { goal_id: shortGoalId, conversation_uid: conversationUid };
      }
      if (legacyReceiptRead || replyIdRead) return false;
      replyIdRead = true;
      return privateGoalReplyId;
    },
  };
}

function privateGoalPoll(shortGoalId: string, objective: () => string) {
  return async (route: string, init: any) => {
    expect(route).toMatch(/\/goal-replies\/poll$/);
    expect(init).toMatchObject({ method: "POST" });
    expect(JSON.parse(init.body)).toEqual({ goal_reply_id: privateGoalReplyId });
    return {
      status: "complete",
      success: true,
      message: `goal ${shortGoalId} created — turn 1 scheduled\nobjective: ${objective()}`,
    };
  };
}

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
      waitForStreamRowKind: async () => {}, waitForRenderedHubNote: async () => {}, settle: async () => {},
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
      waitForStreamRowKind: async () => {}, waitForRenderedHubNote: async () => {}, settle: async () => {},
      agent: "same", session: "session", LABEL: "test", tag: "tag", U: () => "u", R: () => "r", tools: () => "tools", say: () => "say",
    });
    await expect(flow()).rejects.toThrow(/user row/i);
  });

  it("rejects changes to non-rendered fields of an existing stream user row", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const start = source.indexOf('    async "hub-note"() {');
    const body = source.slice(start, source.indexOf("\n  };", start));
    let status = "started";
    const rec = { net: [], snapshotUserRows: () => [{ id: 1, text: "unchanged" }], snapshotStreamUserRows: () => [{ id: "u", role: "user", status, parts: [{ type: "text", text: "unchanged" }] }] };
    const flow = runInNewContext(`({${body}})["hub-note"]`, {
      send: async () => {}, waitForRunning: async () => {}, sleep: async () => {},
      page: { evaluate: async (fn: Function, arg: unknown) => fn(arg) },
      document: { querySelector: () => ({}) }, window: { __rec: rec },
      apiJson: async (route: string) => route.startsWith("/sessions/") ? { identity: "same", daemon_pid: 42 } : { agents: [{ identity: "same", pid: 42, agent_id: "right" }] },
      postFromClient: async () => { status = "changed"; }, waitForStreamRowKind: async () => ({ row: "note-1" }),
      waitForRenderedHubNote: async () => {}, settle: async () => {},
      agent: "same", session: "session", LABEL: "test", tag: "tag", U: () => "u", R: () => "r", tools: () => "tools", say: () => "say",
    });
    await expect(flow()).rejects.toThrow(/user row/i);
  });

  it("waits for the injected note to render before checking user rows", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const start = source.indexOf('    async "hub-note"() {');
    const body = source.slice(start, source.indexOf("\n  };", start));
    let rendered = false;
    const flow = runInNewContext(`({${body}})["hub-note"]`, {
      send: async () => {}, waitForRunning: async () => {}, sleep: async () => {},
      page: { evaluate: async (fn: Function, arg: unknown) => fn(arg) },
      document: { querySelector: () => ({}) }, window: { __rec: { net: [], snapshotUserRows: () => [] } },
      apiJson: async (route: string) => route.startsWith("/sessions/") ? { identity: "same", daemon_pid: 42 } : { agents: [{ identity: "same", pid: 42, agent_id: "right" }] },
      postFromClient: async () => {}, waitForStreamRowKind: async () => ({ row: "note-1" }),
      waitForRenderedHubNote: async () => { rendered = true; }, settle: async () => {},
      agent: "same", session: "session", LABEL: "test", tag: "tag", U: () => "u", R: () => "r", tools: () => "tools", say: () => "say",
    });
    await flow();
    expect(rendered).toBe(true);
  });

  it("requires the same persisted native goal and running origin across reload", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const start = source.indexOf('    async "goal-reload"() {');
    const body = source.slice(start, source.indexOf("    // Inject a real hub", start));
    const origin = { kind: "goal", goal_id: "goal-full", attempt_id: "attempt-full" };
    const turn = { turn_id: "turn-full", state: "running", input_ids: [], origin };
    const stream = { conversation_uid: "conv-full", turns: [turn], inputs: [] };
    const makePersisted = (text: string) => ({
      conversation_uid: "conv-full",
      goal: {
        goal_id: "goal-full", objective: text.slice("/goal ".length), status: "active",
        attempt: { attempt_id: "attempt-full", state: "dispatched", turn_id: "turn-full" },
      },
    });
    let after: any = null;
    const command = async (text: string) => {
      after = text === "/goal clear" ? null : makePersisted(text);
    };
    const flow = runInNewContext(`({${body}})["goal-reload"]`, {
      activeRuntime: "native", command, mark: async () => {}, hasMatchingGoalTurn, session: "test-session",
      page: privateGoalPage("goal-ful", "conv-full"),
      apiJson: privateGoalPoll("goal-ful", () => after?.goal?.objective ?? ""),
      readPersistedGoal: async () => after, currentStreamState: async () => stream,
      reloadDuringRun: async (_reply: string, check: any) => {
        await check.before(turn, stream);
        after = null;
        await check.after(turn, stream, stream);
      }, U: () => "u", R: () => "r",
    });
    await expect(flow()).rejects.toThrow(/goal reload proof inconclusive/i);
    let successAfter: any = null;
    const successCommand = async (text: string) => {
      successAfter = text === "/goal clear" ? null : makePersisted(text);
    };
    const success = runInNewContext(`({${body}})["goal-reload"]`, {
      activeRuntime: "native", command: successCommand, mark: async () => {}, hasMatchingGoalTurn, session: "test-session",
      page: privateGoalPage("goal-ful", "conv-full"),
      apiJson: privateGoalPoll("goal-ful", () => successAfter?.goal?.objective ?? ""),
      readPersistedGoal: async () => successAfter,
      currentStreamState: async () => stream,
      sleep: async () => {},
      reloadDuringRun: async (_reply: string, check: any) => {
        await check.before(turn, stream);
        await check.after(turn, stream, stream);
      }, U: () => "u", R: () => "r",
    });
    await expect(success()).rejects.toThrow(/rendered.*turn/i);
  });

  it("refuses goal reload when a preexisting goal is active and leaves it untouched", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const start = source.indexOf('    async "goal-reload"() {');
    const body = source.slice(start, source.indexOf("    // Inject a real hub", start));
    const origin = { kind: "goal", goal_id: "preexisting-goal", attempt_id: "preexisting-attempt" };
    const turn = { turn_id: "preexisting-turn", state: "running", input_ids: [], origin };
    const stream = { conversation_uid: "conv-existing", turns: [turn], inputs: [] };
    const preexisting = {
      conversation_uid: "conv-existing",
      goal: {
        goal_id: "preexisting-goal", objective: "a goal owned by the user", status: "active",
        attempt: { attempt_id: "preexisting-attempt", state: "dispatched", turn_id: "preexisting-turn" },
      },
    };
    let persisted = preexisting;
    const commands: string[] = [];
    const flow = runInNewContext(`({${body}})["goal-reload"]`, {
      activeRuntime: "native",
      session: "test-session",
      command: async (text: string) => {
        commands.push(text);
        if (text === "/goal clear") persisted = null;
      },
      mark: async () => {},
      hasMatchingGoalTurn,
      readPersistedGoal: async () => persisted,
      currentStreamState: async () => stream,
      sleep: async () => {},
      reloadDuringRun: async (_reply: string, check: any) => {
        await check.before(turn, stream);
        await check.after(turn, stream, stream);
      },
      page: { evaluate: async () => true },
      U: () => "recorder-objective",
      R: () => "recorder-reply",
    });

    const failure = await flow().then(() => null, (error: unknown) => error);
    expect({ commands, persisted, failure }).toEqual({
      commands: [],
      persisted: preexisting,
      failure: expect.objectContaining({ message: expect.stringMatching(/active goal/i) }),
    });
  });

  it("does not clear a different goal that replaces the recorder-owned goal", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const start = source.indexOf('    async "goal-reload"() {');
    const body = source.slice(start, source.indexOf("    // Inject a real hub", start));
    const origin = { kind: "goal", goal_id: "recorder-goal", attempt_id: "recorder-attempt" };
    const turn = { turn_id: "recorder-turn", state: "running", input_ids: [], origin };
    const stream = { conversation_uid: "conv-owned", turns: [turn], inputs: [] };
    const makePersisted = (goalId: string, objective: string, attemptId: string, turnId: string) => ({
      conversation_uid: "conv-owned",
      goal: {
        goal_id: goalId, objective, status: "active",
        attempt: { attempt_id: attemptId, state: "dispatched", turn_id: turnId },
      },
    });
    const foreignGoal = makePersisted("foreign-goal", "another goal", "foreign-attempt", "foreign-turn");
    let persisted: any = null;
    const commands: string[] = [];
    const marks: string[] = [];
    const flow = runInNewContext(`({${body}})["goal-reload"]`, {
      activeRuntime: "native",
      session: "test-session",
      command: async (text: string) => {
        commands.push(text);
        if (text === "/goal clear") persisted = null;
        else persisted = makePersisted("recorder-goal", text.slice("/goal ".length), "recorder-attempt", "recorder-turn");
      },
      mark: async (name: string) => { marks.push(name); },
      readPersistedGoal: async () => persisted,
      currentStreamState: async () => stream,
      reloadDuringRun: async (_reply: string, check: any) => {
        await check.before(turn, stream);
        persisted = foreignGoal;
        await check.after(turn, stream, stream);
      },
      hasMatchingGoalTurn: () => true,
      page: privateGoalPage("recorder", "conv-owned"),
      apiJson: privateGoalPoll("recorder", () => persisted?.goal?.objective ?? ""),
      sleep: async () => {},
      U: () => "recorder-objective",
      R: () => "recorder-reply",
    });

    const failure = await flow().then(() => null, (error: unknown) => error);
    expect({ commands, persisted, failure }).toEqual({
      commands: [expect.stringMatching(/^\/goal /)],
      persisted: foreignGoal,
      failure: expect.objectContaining({ message: expect.stringMatching(/refus|changed|inconclusive/i) }),
    });
  });

  it("clears the same owned goal after its attempt identity changes", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const start = source.indexOf('    async "goal-reload"() {');
    const body = source.slice(start, source.indexOf("    // Inject a real hub", start));
    const turn = { turn_id: "turn-first", state: "running", input_ids: [], origin: { kind: "goal", goal_id: "goal-full", attempt_id: "attempt-first" } };
    const stream = { conversation_uid: "conv-owned", turns: [turn], inputs: [] };
    const makePersisted = (attemptId: string, turnId: string, objective: string) => ({
      conversation_uid: "conv-owned",
      goal: { goal_id: "goal-full", objective, status: "active", attempt: { attempt_id: attemptId, turn_id: turnId } },
    });
    let persisted: any = null;
    let objective = "";
    const commands: string[] = [];
    const marks: string[] = [];
    const flow = runInNewContext(`({${body}})["goal-reload"]`, {
      activeRuntime: "native", session: "test-session",
      command: async (text: string) => {
        commands.push(text);
        if (text === "/goal clear") persisted = null;
        else {
          objective = text.slice("/goal ".length);
          persisted = makePersisted("attempt-first", "turn-first", objective);
        }
      },
      mark: async (name: string) => { marks.push(name); },
      page: privateGoalPage("goal-ful", "conv-owned"),
      apiJson: privateGoalPoll("goal-ful", () => persisted?.goal?.objective ?? ""),
      readPersistedGoal: async () => persisted,
      currentStreamState: async () => stream,
      hasMatchingGoalTurn: () => true,
      reloadDuringRun: async (_reply: string, check: any) => {
        await check.before(turn, stream);
        persisted = makePersisted("attempt-next", "turn-next", objective);
        await check.after(turn, stream, stream);
      },
      sleep: async () => {},
      U: () => "recorder-objective", R: () => "recorder-reply",
    });

    await expect(flow()).rejects.toThrow(/proof inconclusive|changed during reload/i);
    expect(commands).toEqual([expect.stringMatching(/^\/goal /), "/goal clear"]);
    expect(persisted).toBeNull();
    expect(JSON.stringify(marks)).not.toContain(objective);
  });

  it("claims a goal from its private reply when the creation event was missed", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const start = source.indexOf('    async "goal-reload"() {');
    const body = source.slice(start, source.indexOf("    // Inject a real hub", start));
    const goal = { conversation_uid: "conv-owned", goal: { goal_id: "goal-full", status: "active", attempt: { attempt_id: "attempt-one", turn_id: "turn-one" } } };
    let persisted: any = null;
    let objective = "";
    let clock = 0;
    let polls = 0;
    let reloads = 0;
    const commands: string[] = [];
    const privateMessage = () => `goal goal-ful created — turn 1 scheduled\nobjective: ${objective}`;
    const flow = runInNewContext(`({${body}})["goal-reload"]`, {
      Date: { now: () => clock },
      activeRuntime: "native", session: "test-session",
      command: async (text: string) => {
        commands.push(text);
        if (text === "/goal clear") persisted = null;
        else { objective = text.slice("/goal ".length); persisted = goal; }
      },
      mark: async () => {},
      page: { evaluate: async (_fn: unknown, marker?: unknown) => typeof marker === "string" ? null : privateGoalReplyId },
      apiJson: async (route: string, init: any) => {
        polls += 1;
        expect(route).toBe("/sessions/test-session/goal-replies/poll");
        expect(JSON.parse(init.body)).toEqual({ goal_reply_id: privateGoalReplyId });
        return { status: "complete", success: true, message: privateMessage() };
      },
      readPersistedGoal: async () => persisted,
      currentStreamState: async () => ({ conversation_uid: "conv-owned", turns: [], inputs: [] }),
      reloadDuringRun: async () => { reloads += 1; throw new Error("injected proof failure"); },
      sleep: async (ms: number) => { clock += ms; },
      U: () => "recorder-objective", R: () => "recorder-reply",
    });

    await expect(flow()).rejects.toThrow(/injected proof failure/i);
    expect({ polls, reloads, commands, persisted }).toEqual({
      polls: 1, reloads: 1, commands: [expect.stringMatching(/^\/goal /), "/goal clear"], persisted: null,
    });
    expect(JSON.stringify(commands)).not.toContain(privateGoalReplyId);
  });

  it("does not clear or resubmit when a concurrent goal replaces the private receipt target", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const start = source.indexOf('    async "goal-reload"() {');
    const body = source.slice(start, source.indexOf("    // Inject a real hub", start));
    const foreignGoal = { conversation_uid: "conv-other", goal: { goal_id: "foreign-full", status: "active", attempt: { attempt_id: "foreign-attempt", turn_id: "foreign-turn" } } };
    let objective = "";
    let clock = 0;
    let pollCount = 0;
    let persisted: any = null;
    const commands: string[] = [];
    const flow = runInNewContext(`({${body}})["goal-reload"]`, {
      Date: { now: () => clock },
      activeRuntime: "native", session: "test-session",
      command: async (text: string) => {
        commands.push(text);
        if (text === "/goal clear") persisted = null;
        else { objective = text.slice("/goal ".length); persisted = foreignGoal; }
      },
      mark: async () => {},
      page: { evaluate: async (_fn: unknown, marker?: unknown) => typeof marker === "string" ? null : privateGoalReplyId },
      apiJson: async (route: string, init: any) => {
        pollCount += 1;
        expect(route).toBe("/sessions/test-session/goal-replies/poll");
        expect(JSON.parse(init.body)).toEqual({ goal_reply_id: privateGoalReplyId });
        return { status: "complete", success: true, message: `goal owned-by-run created\nobjective: ${objective}` };
      },
      readPersistedGoal: async () => persisted,
      sleep: async (ms: number) => { clock += ms; },
      U: () => "recorder-objective", R: () => "recorder-reply",
    });

    const failure = await flow().then(() => null, (error: unknown) => error);
    expect({ pollCount, commands, persisted, failure }).toMatchObject({
      pollCount: 1,
      commands: [expect.stringMatching(/^\/goal /)],
      persisted: foreignGoal,
      failure: expect.objectContaining({ message: expect.stringMatching(/refus|claim|identity/i) }),
    });
  });

  it("does not claim a matching private receipt in a different conversation", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const start = source.indexOf('    async "goal-reload"() {');
    const body = source.slice(start, source.indexOf("    // Inject a real hub", start));
    const foreignGoal = {
      conversation_uid: "conv-other",
      goal: { goal_id: "goal-full", objective: "", status: "active", attempt: { attempt_id: "foreign-attempt", turn_id: "foreign-turn" } },
    };
    let persisted: any = null;
    let objective = "";
    const commands: string[] = [];
    const flow = runInNewContext(`({${body}})["goal-reload"]`, {
      activeRuntime: "native", session: "test-session",
      command: async (text: string) => {
        commands.push(text);
        if (text === "/goal clear") persisted = null;
        else {
          objective = text.slice("/goal ".length);
          foreignGoal.goal.objective = objective;
          persisted = foreignGoal;
        }
      },
      mark: async () => {},
      page: privateGoalPage("goal-ful", "conv-other"),
      apiJson: privateGoalPoll("goal-ful", () => objective),
      readPersistedGoal: async () => persisted,
      currentStreamState: async () => ({ conversation_uid: "conv-owned", turns: [], inputs: [] }),
      sleep: async () => {},
      U: () => "recorder-objective", R: () => "recorder-reply",
    });

    const failure = await flow().then(() => null, (error: unknown) => error);
    expect({ commands, persisted, failure }).toMatchObject({
      commands: [expect.stringMatching(/^\/goal /)],
      persisted: foreignGoal,
      failure: expect.objectContaining({ message: expect.stringMatching(/conversation|claim|identity/i) }),
    });
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

  it("clears an unfinished native goal when reload proof fails", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const start = source.indexOf('    async "goal-reload"() {');
    const body = source.slice(start, source.indexOf("    // Inject a real hub", start));
    const origin = { kind: "goal", goal_id: "goal-full", attempt_id: "attempt-full" };
    const turn = { turn_id: "turn-full", state: "running", input_ids: [], origin };
    const stream = { conversation_uid: "conv-full", turns: [turn], inputs: [] };
    let persisted: any = null;
    const commands: string[] = [];
    let cleared = false;
    const flow = runInNewContext(`({${body}})["goal-reload"]`, {
      activeRuntime: "native",
      session: "test-session",
      command: async (text: string) => {
        commands.push(text);
        if (text === "/goal clear") {
          cleared = true;
          return;
        }
        persisted = {
          conversation_uid: "conv-full",
          goal: {
            goal_id: "goal-full", objective: text.slice("/goal ".length), status: "active",
            attempt: { attempt_id: "attempt-full", state: "dispatched", turn_id: "turn-full" },
          },
        };
      },
      mark: async () => {},
      hasMatchingGoalTurn: () => false,
      readPersistedGoal: async () => cleared ? null : persisted,
      currentStreamState: async () => stream,
      page: privateGoalPage("goal-ful", "conv-full"),
      apiJson: privateGoalPoll("goal-ful", () => persisted?.goal?.objective ?? ""),
      reloadDuringRun: async (_reply: string, check: any) => {
        await check.before(turn, stream);
      },
      sleep: async () => {},
      U: () => "u",
      R: () => "r",
    });

    await expect(flow()).rejects.toThrow(/before reload/i);
    expect(commands[1]).toBe("/goal clear");
    expect(commands).toHaveLength(2);
  });

  it("reports a cleanup timeout when an unfinished goal remains active", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const start = source.indexOf('    async "goal-reload"() {');
    const body = source.slice(start, source.indexOf("    // Inject a real hub", start));
    const origin = { kind: "goal", goal_id: "goal-full", attempt_id: "attempt-full" };
    const turn = { turn_id: "turn-full", state: "running", input_ids: [], origin };
    const stream = { conversation_uid: "conv-full", turns: [turn], inputs: [] };
    let persisted: any = null;
    let clock = 0;
    const commands: string[] = [];
    const flow = runInNewContext(`({${body}})["goal-reload"]`, {
      Date: { now: () => clock },
      activeRuntime: "native",
      session: "test-session",
      command: async (text: string) => {
        commands.push(text);
        if (text !== "/goal clear") {
          persisted = {
            conversation_uid: "conv-full",
            goal: {
              goal_id: "goal-full", objective: text.slice("/goal ".length), status: "active",
              attempt: { attempt_id: "attempt-full", state: "dispatched", turn_id: "turn-full" },
            },
          };
        }
      },
      mark: async () => {},
      hasMatchingGoalTurn: () => false,
      readPersistedGoal: async () => persisted,
      currentStreamState: async () => stream,
      page: privateGoalPage("goal-ful", "conv-full"),
      apiJson: privateGoalPoll("goal-ful", () => persisted?.goal?.objective ?? ""),
      reloadDuringRun: async (_reply: string, check: any) => {
        await check.before(turn, stream);
      },
      sleep: async (ms: number) => { clock += ms; },
      U: () => "u",
      R: () => "r",
    });

    await expect(flow()).rejects.toThrow(/goal reload cleanup failed/i);
    expect(commands).toContain("/goal clear");
  });

  it("documents native-only goal reload evidence and the ACP limitation", () => {
    const docs = readFileSync(
      new URL("../../../../docs/quality/webui-browser-coverage.md", import.meta.url),
      "utf8",
    );
    const row = docs.split("\n").find((line) => line.startsWith("| `goal-reload` |"));

    expect(row).toBeDefined();
    expect(row).toContain("native `/goal`");
    expect(row).toContain("ACP reports unsupported");
    expect(row).toContain("GET /sessions/{id}/goal");
    expect(row).toContain("turn.state.origin");
    expect(row).toContain("data-chat-*");
    expect(row).not.toContain("equivalent goal-directed chat turn");
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

  it("permits an inputless goal turn only when explicitly requested and retains its identity", () => {
    const turn = { turn_id: "goal-turn", input_ids: [] };
    const snapshot = { turns: [{ ...turn, state: "running" }], inputs: [] };
    expect(hasRunningTurnWithInputs(snapshot, turn)).toBe(false);
    expect(hasRunningTurnWithInputs(snapshot, turn, { allowEmptyInputs: true })).toBe(true);
    expect(hasRunningTurnWithInputs({ ...snapshot, turns: [{ ...turn, turn_id: "other", state: "running" }] }, turn, { allowEmptyInputs: true })).toBe(false);
    expect(hasRunningTurnWithInputs({ ...snapshot, turns: [{ ...turn, state: "ended" }] }, turn, { allowEmptyInputs: true })).toBe(false);
  });

  it("matches persisted full goal and attempt identity to the running stream across reload", () => {
    const origin = { kind: "goal", goal_id: "goal-full-123", attempt_id: "attempt-full-456" };
    const turn = { turn_id: "turn-full", state: "running", input_ids: [], origin };
    const persisted = { conversation_uid: "conv-full", goal: { goal_id: origin.goal_id, status: "active", attempt: { attempt_id: origin.attempt_id, state: "dispatched", turn_id: turn.turn_id } } };
    const snapshot = { conversation_uid: "conv-full", turns: [turn], inputs: [] };
    expect(hasMatchingGoalTurn(snapshot, persisted, turn)).toBe(true);
    expect(hasMatchingGoalTurn({ ...snapshot, turns: [{ ...turn, input_ids: ["joined"] }], inputs: [{ id: "joined", turn_id: turn.turn_id, state: "joined" }] }, persisted, turn)).toBe(true);
    expect(hasMatchingGoalTurn({ ...snapshot, conversation_uid: "other" }, persisted, turn)).toBe(false);
    expect(hasMatchingGoalTurn({ ...snapshot, turns: [{ ...turn, state: "ended" }] }, persisted, turn)).toBe(false);
    expect(hasMatchingGoalTurn({ ...snapshot, turns: [{ ...turn, origin: { ...origin, attempt_id: "other" } }] }, persisted, turn)).toBe(false);
    expect(hasMatchingGoalTurn(snapshot, { ...persisted, goal: { ...persisted.goal, attempt: { ...persisted.goal.attempt, turn_id: "other" } } }, turn)).toBe(false);
    expect(hasMatchingGoalTurn(snapshot, { ...persisted, goal: { ...persisted.goal, goal_id: "goal-ful" } }, turn)).toBe(false);
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

  it("does not report a goal run clean without turn-attributed rendered tool cards", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const start = source.indexOf('    async "goal-reload"() {');
    const body = source.slice(start, source.indexOf('    // Inject a real hub message', start));
    const turn = { turn_id: "goal-turn", input_ids: [], origin: { type: "goal" } };
    const goal = { conversation_uid: "conversation", goal: { goal_id: "g", attempt: { attempt_id: "a", turn_id: "goal-turn" } } };
    const stream = { conversation_uid: "conversation", turns: [turn] };
    let persisted: any = null;
    const flow = runInNewContext(`({${body}})["goal-reload"]`, {
      activeRuntime: "native", session: "test-session", command: async (text: string) => {
        if (text === "/goal clear") {
          persisted = null;
        } else if (text.startsWith("/goal ")) {
          persisted = {
            ...goal,
            goal: { ...goal.goal, objective: text.slice("/goal ".length) },
          };
        }
      }, U: () => "u", R: () => "r",
      page: privateGoalPage("g", "conversation"),
      apiJson: privateGoalPoll("g", () => persisted?.goal?.objective ?? ""),
      readPersistedGoal: async () => persisted,
      sleep: async () => {},
      currentStreamState: async () => stream,
      hasMatchingGoalTurn: () => true, mark: async () => {},
      reloadDuringRun: async (_reply: string, check: any) => {
        await check.before(turn, stream);
        await check.after(turn, stream, stream);
      },
    });
    await expect(flow()).rejects.toThrow(/rendered.*turn|turn.*rendered/i);
  });

  it("measures goal cards by verified turn and call identity across recovery", () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("function installRecorder(cfg)"), source.indexOf("// ---------------------------------------------------------------- one flow"));
    const installPage = (saved: string | null) => {
      const window = { fetch: async () => new Response() };
      const install = runInNewContext(`(${body})`, {
        window, Request, Response, ReadableStream, TextDecoder, URL, performance,
        location: { href: "http://localhost/" },
        sessionStorage: { getItem: () => saved, removeItem: () => {} }, requestAnimationFrame: () => {},
      });
      install({ wall0: Date.now(), sessionStreamSuffix: "/sessions/target/stream", user: ["joined"], reply: [] });
      return (window as any).__rec;
    };
    const rec = installPage(null);
    rec.mark("PAGE START GOAL");
    rec.noteToolRow({ id: "r1", turn_id: "goal", parts: [{ type: "tool-call", tool_call_id: "c1" }] });
    rec.noteToolRow({ id: "old", turn_id: "other", parts: [{ type: "tool-call", tool_call_id: "old" }] });
    rec.verifyGoalTurn("goal");
    expect(rec.toolsStarted).toBe(1);
    const card = (turn: string, row: string, call: string) => ({ dataset: { chatTurnId: turn, chatRowId: row, chatToolCallId: call } });
    let sample = rec.goalToolSample([card("goal", "r1", "c1"), card("other", "old", "old")]);
    expect(sample).toMatchObject({ toolsStarted: 1, toolsDrawn: 1, duplicateTools: 0 });
    sample = rec.goalToolSample([card("goal", "r1", "c1"), card("goal", "r1", "c1")]);
    expect(sample.duplicateTools).toBe(1);
    expect(analyzeChatFrames([frame({ ...sample })], ready).defects.dupTools).toBe(1);
    const restored = installPage(JSON.stringify(rec));
    restored.noteToolRow({ id: "r1", turn_id: "goal", parts: [{ type: "tool-call", tool_call_id: "c1" }] });
    restored.noteToolRow({ id: "joined", kind: "input", turn_id: "goal", parts: [{ type: "text", text: "joined" }] });
    restored.noteToolRow({ id: "r2", turn_id: "goal", parts: [{ type: "tool-call", tool_call_id: "c2" }] });
    sample = restored.goalToolSample([card("goal", "r1", "c1"), card("goal", "r1", "c1")]);
    expect(sample).toMatchObject({ toolsStarted: 2, toolsDrawn: 2, duplicateTools: 1 });
    expect(analyzeChatFrames([frame({ ...sample })], ready).defects.dupTools).toBe(1);
    expect(restored.goalToolSample([])).toMatchObject({ toolsStarted: 2, toolsDrawn: 0, duplicateTools: 0 });
  });

  it("counts goal tool cards even when their row is unpaired or has no turn attribute", () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("function installRecorder(cfg)"), source.indexOf("// ---------------------------------------------------------------- one flow"));
    const cards = [
      { dataset: { chatRowId: "r1", chatToolCallId: "call-1" } },
      { dataset: { chatTurnId: "goal-turn", chatRowId: "paged-row", chatToolCallId: "call-1" } },
    ];
    let sampledSelector = "";
    const assistant = {
      dataset: { role: "assistant" },
      textContent: "Run · shell",
      parentElement: null,
      isConnected: true,
    };
    const document = {
      querySelectorAll(selector: string) {
        if (selector === "[data-role]") return [assistant];
        if (selector.includes("data-chat-tool-call-id")) {
          sampledSelector = selector;
          const requirements = [
            ["data-chat-tool-call-id", "chatToolCallId"],
            ["data-chat-row-id", "chatRowId"],
            ["data-chat-turn-id", "chatTurnId"],
          ] as const;
          const required = requirements.filter(([attribute]) => selector.includes(`[${attribute}]`));
          return cards.filter((card) => required.every(([, key]) => Boolean(card.dataset[key])));
        }
        return [];
      },
      querySelector: () => null,
    };
    const callbacks: Array<() => void> = [];
    const window = { fetch: async () => new Response() };
    const install = runInNewContext(`(${body})`, {
      window,
      document,
      Request,
      Response,
      ReadableStream,
      TextDecoder,
      URL,
      performance: { timeOrigin: 0, now: () => 2 },
      getComputedStyle: () => ({ opacity: "1", overflowY: "visible" }),
      requestAnimationFrame: (callback: () => void) => { callbacks.push(callback); },
      location: { href: "http://localhost/" },
      sessionStorage: { getItem: () => null, removeItem: () => {} },
    });
    install({ wall0: 2, sessionStreamSuffix: "/sessions/target/stream", user: [], reply: [] });
    const rec = (window as any).__rec;
    rec.mark("PAGE START GOAL");
    rec.noteToolRow({
      id: "r1",
      turn_id: "goal-turn",
      parts: [{ type: "tool-call", tool_call_id: "call-1" }],
    });
    rec.verifyGoalTurn("goal-turn");
    callbacks.shift()?.();

    expect(sampledSelector).toBe("[data-chat-tool-call-id]");
    expect(rec.sampleError).toBeUndefined();
    expect(rec.frames.at(-1)).toMatchObject({ toolsStarted: 1, toolsDrawn: 2, duplicateTools: 1 });
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
    let event: any = { type: "context_compaction_status", status: "idle" };
    const window = { fetch: async (_url: string) => new Response(`data: ${JSON.stringify(event)}\n\n`) };
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
    event = {
      type: "goal.state_changed", goal_id: "goal-short", conversation_uid: "conversation",
      status: "active", kind: "created", objective: "[chat-recorder-marker] objective",
    };
    await (await window.fetch("http://localhost/sessions/target/events")).text();
    const goalEvent = (window as any).__rec.net.find((entry: any) => entry.ev === "goal.state_changed");
    expect(goalEvent).toMatchObject({
      goal_id: "goal-short", goal_conversation_uid: "conversation", goal_status: "active",
      goal_kind: "created",
    });
    expect(goalEvent).not.toHaveProperty("goal_objective");
    const rec = (window as any).__rec;
    rec.mark("PAGE START GOAL");
    await (window.fetch as any)("http://localhost/sessions/target/inputs", {
      method: "POST",
      body: JSON.stringify({
        id: "goal-input", text: "/goal private-objective-marker and instructions", goal_reply_id: privateGoalReplyId,
      }),
    });
    expect(rec.goalReplyId).toBe(privateGoalReplyId);
    expect(JSON.stringify({ net: rec.net, marks: rec.marks })).not.toContain(privateGoalReplyId);
    expect(JSON.stringify({ net: rec.net, marks: rec.marks })).not.toContain("private-objective-marker");
  });

  it("captures the private goal capability from the actual message request without recording it", async () => {
    const source = readFileSync(new URL("../scripts/chat-recorder.mjs", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("function installRecorder(cfg)"), source.indexOf("// ---------------------------------------------------------------- one flow"));
    const privateReply = "goal goal-short created\nobjective: private-objective-marker";
    const window = {
      fetch: async () => new Response(`data: ${JSON.stringify({ type: "token", text: privateReply })}\n\n`),
    };
    const install = runInNewContext(`(${body})`, {
      window, Request, Response, ReadableStream, TextDecoder, URL, performance,
      location: { href: "http://localhost/" },
      sessionStorage: { getItem: () => null, removeItem: () => {} },
      requestAnimationFrame: () => {},
    });
    install({ wall0: Date.now(), sessionStreamSuffix: chatRecorderStreamSuffix("target"), user: [], reply: [] });
    const rec = (window as any).__rec;
    rec.mark("PAGE START GOAL");
    const response = await (window.fetch as any)("http://localhost/sessions/target/message", {
      method: "POST",
      body: JSON.stringify({
        request_id: "goal-input", content: [{ type: "text", text: "/goal private-objective-marker" }],
        goal_reply_id: privateGoalReplyId,
      }),
    });
    await response.text();
    expect(rec.goalReplyId).toBe(privateGoalReplyId);
    expect(JSON.stringify({ net: rec.net, marks: rec.marks })).not.toContain(privateGoalReplyId);
    expect(JSON.stringify({ net: rec.net, marks: rec.marks })).not.toContain("private-objective-marker");
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
