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

  it("name nothing when no send was stamped", () => {
    expect(custom([userRow({})])?.requestIds).toBeUndefined();
  });
});
