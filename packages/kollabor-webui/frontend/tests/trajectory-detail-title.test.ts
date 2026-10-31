import { describe, expect, it } from "vitest";
import type { HistoryMessage } from "../src/api";
import {
  detailTitle,
  projectTrajectory,
} from "../src/components/trajectory/trajectory-records";

// The UI-review repro: `seq 1 2500` ran, its rows flooded the detail title.
const output = Array.from({ length: 2500 }, (_, i) => String(i + 1)).join(" ");

const history: HistoryMessage[] = [
  { role: "user", content: "count to 2500", timestamp: "2026-10-30T17:00:00Z" },
  {
    role: "assistant",
    content: "",
    timestamp: "2026-10-30T17:00:01Z",
    metadata: {
      tool_calls: [
        {
          id: "call-1",
          function: {
            name: "terminal",
            arguments: JSON.stringify({ command: "seq 1 2500" }),
          },
        },
      ],
    },
  },
  {
    role: "tool",
    content: output,
    timestamp: "2026-10-30T17:00:02Z",
    metadata: { tool_call_id: "call-1", tool_name: "terminal" },
  },
];

describe("a tool record's detail title", () => {
  const records = projectTrajectory(history);
  const tool = records.find((record) => record.kind === "tool");

  it("is the tool label and its input summary only", () => {
    expect(tool?.title).toBe("Run · seq 1 2500");
    expect(detailTitle(tool!)).toBe("Run · seq 1 2500");
  });

  it("carries none of the output text, however long it ran", () => {
    const title = detailTitle(tool!);
    expect(title).not.toContain(output.slice(0, 30));
    expect(title).not.toContain(" 1244 1245 1246 ");
    expect(title).not.toContain("…");
  });

  it("leaves the output in the record's output field", () => {
    expect(tool?.output).toBe(output);
  });

  it("keeps every other record kind's title unchanged", () => {
    for (const record of records.filter((item) => item.kind !== "tool")) {
      expect(detailTitle(record)).toBe(record.summary);
    }
    const user = records.find((record) => record.kind === "user");
    expect(detailTitle(user!)).toBe("count to 2500");
  });
});
