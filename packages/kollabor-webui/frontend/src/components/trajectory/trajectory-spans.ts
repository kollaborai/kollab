import type { SpanData } from "@assistant-ui/react-o11y";
import type { TrajectoryRecord } from "./trajectory-records";

export type TraceRange = { min: number; max: number };

export interface Trace {
  spans: SpanData[];
  /** Each span's turn window, so every turn reads as its own waterfall. */
  ranges: Map<string, TraceRange>;
  /** Spans that are records; synthetic turn roots select nothing. */
  recordIds: Set<string>;
  /** Records without a usable timestamp, left out of the waterfall. */
  untimed: number;
}

const isTool = (record: TrajectoryRecord) =>
  record.kind === "tool" || record.kind === "tool-batch";

const parse = (timestamp: string | null) =>
  timestamp ? Date.parse(timestamp) : Number.NaN;

/**
 * The trajectory as a trace: the prompt that opens a turn is its root, the
 * turn's model requests hang under it and each request's tool calls under
 * the request. The daemon writes a request's assistant message after its
 * tools ran (sequentially), so a request lays out backwards from that
 * timestamp: tools back to back ending there, the model call ending where
 * the first tool starts. Other records end at their timestamp. The prompt is
 * a point: the gap before it is the person, not the agent.
 */
export function traceSpans(records: TrajectoryRecord[]): Trace {
  const spans: SpanData[] = [];
  const recordIds = new Set<string>();
  const roots = new Map<number, SpanData>();
  const rootOf = new Map<string, SpanData>();
  const requests = new Map<string, string>();
  const toolMs = new Map<string, number>();
  const cursor = new Map<string, number>();
  const keyOf = (record: TrajectoryRecord) => `${record.turn}:${record.request}`;
  let untimed = 0;
  let previous = Number.NEGATIVE_INFINITY;

  for (const record of records) {
    if (isTool(record) && record.request !== null) {
      const key = keyOf(record);
      toolMs.set(key, (toolMs.get(key) ?? 0) + (record.durationSeconds ?? 0) * 1000);
    }
  }

  for (const record of records) {
    const stamp = parse(record.timestamp);
    if (!Number.isFinite(stamp)) {
      untimed += 1;
      continue;
    }
    const key = keyOf(record);
    const ms = Math.max(0, record.durationSeconds ?? 0) * 1000;
    let start: number;
    let end: number;
    if (record.opensTurn) {
      start = end = stamp;
    } else if (record.kind === "assistant") {
      end = stamp - (toolMs.get(key) ?? 0);
      // Never before the record ahead of it in the history.
      start = Math.max(end - ms, Math.min(previous, end));
      cursor.set(key, end);
    } else if (isTool(record) && cursor.has(key)) {
      start = cursor.get(key)!;
      end = start + ms;
      cursor.set(key, end);
    } else {
      end = stamp;
      start = end - ms;
    }
    previous = stamp;

    let root = roots.get(record.turn);
    if (!root) {
      root = {
        id: record.opensTurn ? record.id : `turn:${record.turn}`,
        parentSpanId: null,
        name: record.opensTurn
          ? record.summary
          : record.turn
            ? `Turn ${record.turn}`
            : "Session Start",
        type: "turn",
        status: "completed",
        startedAt: start,
        endedAt: end,
        latencyMs: 0,
      };
      roots.set(record.turn, root);
      rootOf.set(root.id, root);
      spans.push(root);
      if (record.opensTurn) {
        recordIds.add(record.id);
        continue;
      }
    }

    root.startedAt = Math.min(root.startedAt, start);
    root.endedAt = Math.max(root.endedAt ?? end, end);
    if (record.isError) root.status = "failed";

    const parentSpanId =
      isTool(record) && requests.has(key) ? requests.get(key)! : root.id;
    if (record.kind === "assistant") requests.set(key, record.id);

    spans.push({
      id: record.id,
      parentSpanId,
      name: record.summary,
      type: record.kind,
      status: record.isError ? "failed" : "completed",
      startedAt: start,
      endedAt: end,
      latencyMs: end - start,
    });
    recordIds.add(record.id);
    rootOf.set(record.id, root);
  }

  const ranges = new Map<string, TraceRange>();
  for (const root of roots.values()) {
    root.latencyMs = (root.endedAt ?? root.startedAt) - root.startedAt;
  }
  for (const [id, root] of rootOf) {
    ranges.set(id, { min: root.startedAt, max: root.endedAt ?? root.startedAt });
  }
  return { spans, ranges, recordIds, untimed };
}
