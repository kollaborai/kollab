import { describe, expect, it } from "vitest";
import type { HistoryMessage } from "../src/api";
import { buildInitialState } from "../src/runtime";

// #1359: a saved user row names the sends it carries, so the page tells its
// own send from another sender's identical words.
const userRow = (metadata: Record<string, unknown>): HistoryMessage =>
  ({ role: "user", content: "hello", metadata } as unknown as HistoryMessage);
const custom = (history: HistoryMessage[]) =>
  buildInitialState("s1", [], history).messages[0]?.metadata?.custom as Record<string, unknown> | undefined;

describe("saved user rows", () => {
  it("carry the request id of each send they hold", () => {
    expect(custom([userRow({ operator_inputs: [
      { source: "state_rpc", timestamp: "t1", text: "hello", request_id: "r1" },
      { source: "user", timestamp: "t2", text: "typed" },
      { source: "state_rpc", timestamp: "t3", text: "again", request_id: "r2" },
    ] })])?.requestIds).toEqual(["r1", "r2"]);
  });

  it("carry the row's own request id (an ACP admission)", () => {
    expect(custom([userRow({ provider: "acp", request_id: "acp-1" })])?.requestIds).toEqual(["acp-1"]);
  });

  it("show one row per send when they were saved as one", () => {
    // The daemon saves sends that arrive together as one row (behind the
    // agent HUD); each shows as its own message, as it was sent.
    const rows = buildInitialState("s1", [], [{
      role: "user", content: "<agent_hud>\nbudget\n</agent_hud>\nmine\ntheirs",
      metadata: { operator_inputs: [
        { source: "state_rpc", timestamp: "t1", text: "mine", request_id: "r1" },
        { source: "state_rpc", timestamp: "t2", text: "theirs", request_id: "r2" },
      ] },
    } as unknown as HistoryMessage]).messages;
    expect(rows.map((row) => [row.id, row.content, (row.metadata?.custom as Record<string, unknown>)?.requestIds]))
      .toEqual([
        ["history-0-send-0", [{ type: "text", text: "mine" }], ["r1"]],
        ["history-0-send-1", [{ type: "text", text: "theirs" }], ["r2"]],
      ]);
  });

  it("stay one row when the sends' words are not what it shows", () => {
    const rows = buildInitialState("s1", [], [{
      role: "user", content: "edited words",
      metadata: { operator_inputs: [
        { source: "state_rpc", timestamp: "t1", text: "mine", request_id: "r1" },
        { source: "state_rpc", timestamp: "t2", text: "theirs", request_id: "r2" },
      ] },
    } as unknown as HistoryMessage]).messages;
    expect(rows.map((row) => row.id)).toEqual(["history-0"]);
    expect((rows[0].metadata?.custom as Record<string, unknown>)?.requestIds).toEqual(["r1", "r2"]);
  });

  it("name nothing when no send was stamped", () => {
    expect(custom([userRow({})])?.requestIds).toBeUndefined();
  });
});
