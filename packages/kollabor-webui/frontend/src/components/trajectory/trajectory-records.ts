import type { HistoryMessage } from "@/api";
import { isToolOutputBatch } from "@/api";

export type TrajectoryRecordKind =
  | "system"
  | "user"
  | "assistant"
  | "tool"
  | "tool-batch"
  | "message";

export interface TrajectoryRecord {
  id: string;
  kind: TrajectoryRecordKind;
  index: number;
  turn: number;
  request: number | null;
  title: string;
  summary: string;
  input?: string;
  output?: string;
  thinking?: string;
  timestamp: string | null;
  durationSeconds: number | null;
  isError?: boolean;
  opensTurn?: boolean;
  sourceIndex: number;
  callId?: string;
}

type JsonObject = Record<string, unknown>;

type ToolCall = {
  id: string;
  name: string;
  input: string;
};

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function firstLine(value: string): string {
  const line = value.trim().split(/\r?\n/, 1)[0]?.trim();
  return line || "(empty)";
}

export function previewText(value: string, maxLength = 160): string {
  const compact = value.replace(/\s+/g, " ").trim();
  if (!compact) return "(empty)";
  return compact.length > maxLength
    ? `${compact.slice(0, maxLength - 1)}…`
    : compact;
}

function prettyValue(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

function parseJson(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return value;
  }
}

function metadataFor(message: HistoryMessage): JsonObject {
  return isObject(message.metadata) ? message.metadata : {};
}

function messageIdentity(message: HistoryMessage, sourceIndex: number): string {
  const metadata = metadataFor(message);
  const identity =
    asString(metadata.id) ??
    asString(metadata.message_id) ??
    asString(metadata.source_seq) ??
    (typeof metadata.seq === "number" ? String(metadata.seq) : undefined);
  return identity ? `history:${identity}` : `history:${sourceIndex}`;
}

function normalizeTimestamp(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function durationBetween(
  current: string | null,
  previous: string | null,
): number | null {
  if (!current || !previous) return null;
  const currentMs = Date.parse(current);
  const previousMs = Date.parse(previous);
  if (!Number.isFinite(currentMs) || !Number.isFinite(previousMs)) return null;
  if (currentMs < previousMs) return null;
  return (currentMs - previousMs) / 1000;
}

function readBoolean(metadata: JsonObject, keys: string[]): boolean | undefined {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === "boolean") return value;
    if (value === "true") return true;
    if (value === "false") return false;
  }
  return undefined;
}

function toolCallId(metadata: JsonObject): string | undefined {
  for (const key of ["tool_call_id", "toolCallId", "call_id", "tool_id"]) {
    const value = asString(metadata[key]);
    if (value) return value;
  }
  return undefined;
}

function toolOutput(message: HistoryMessage, metadata: JsonObject): string {
  if (typeof message.content === "string") return message.content;
  for (const key of [
    "tool_output",
    "tool_output_content",
    "tool_output_result",
    "output",
    "result",
  ]) {
    const value = metadata[key];
    if (value !== undefined && value !== null) return prettyValue(value);
  }
  return "";
}

function toolCallRecords(message: HistoryMessage, sourceIndex: number): ToolCall[] {
  const rawCalls = metadataFor(message).tool_calls;
  if (!Array.isArray(rawCalls)) return [];

  return rawCalls.map((rawCall, callIndex) => {
    const call = isObject(rawCall) ? rawCall : {};
    const functionValue = isObject(call.function) ? call.function : {};
    const id =
      asString(call.id) ??
      asString(call.call_id) ??
      asString(call.tool_call_id) ??
      `history-${sourceIndex}-tool-${callIndex}`;
    const name =
      asString(functionValue.name) ?? asString(call.name) ?? "tool";
    const rawInput = functionValue.arguments ?? call.arguments ?? {};
    return {
      id,
      name,
      input: prettyValue(parseJson(rawInput)),
    };
  });
}

function parseToolBatch(content: string): string[] {
  const lines = content
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) return ["(empty tool result)"];

  return lines.map((line) =>
    line.replace(/^Tool result:\s*/i, "").trim() || "(empty tool result)",
  );
}

export function projectTrajectory(history: HistoryMessage[]): TrajectoryRecord[] {
  const records: TrajectoryRecord[] = [];
  const nativeCalls = new Map<string, TrajectoryRecord>();
  let turn = 0;
  let request = 0;
  let previousTimestamp: string | null = null;

  const add = (
    record: Omit<TrajectoryRecord, "index" | "durationSeconds">,
  ): TrajectoryRecord => {
    const next: TrajectoryRecord = {
      ...record,
      index: records.length + 1,
      durationSeconds: durationBetween(record.timestamp, previousTimestamp),
    };
    records.push(next);
    previousTimestamp = record.timestamp;
    return next;
  };

  history.forEach((message, sourceIndex) => {
    const metadata = metadataFor(message);
    const timestamp = normalizeTimestamp(message.timestamp);
    const content = message.content || "";
    const identity = messageIdentity(message, sourceIndex);

    if (message.role === "system") {
      add({
        id: `${identity}:system`,
        kind: "system",
        turn: 0,
        request: null,
        title: "SYSTEM",
        summary: firstLine(content),
        output: content,
        timestamp,
        sourceIndex,
      });
      return;
    }

    if (message.role === "user" && isToolOutputBatch(message)) {
      parseToolBatch(content).forEach((result, resultIndex) => {
        add({
          id: `${identity}:tool-batch:${resultIndex}`,
          kind: "tool-batch",
          turn,
          request: request || null,
          title: "TOOL RESULT",
          summary: previewText(result),
          output: result,
          timestamp,
          sourceIndex,
          callId: `batch:${sourceIndex}:${resultIndex}`,
        });
      });
      return;
    }

    if (message.role === "user") {
      turn += 1;
      add({
        id: `${identity}:user`,
        kind: "user",
        turn,
        request: null,
        title: "USER",
        summary: firstLine(content),
        input: content,
        timestamp,
        opensTurn: true,
        sourceIndex,
      });
      return;
    }

    if (message.role === "assistant") {
      request += 1;
      add({
        id: `${identity}:assistant`,
        kind: "assistant",
        turn,
        request,
        title: "ASSISTANT",
        summary: firstLine(content),
        output: content,
        thinking: message.thinking || undefined,
        timestamp,
        sourceIndex,
      });

      for (const [callIndex, call] of toolCallRecords(
        message,
        sourceIndex,
      ).entries()) {
        const record = add({
          id: `${identity}:tool:${call.id || callIndex}`,
          kind: "tool",
          turn,
          request,
          title: call.name,
          summary: call.name,
          input: call.input,
          timestamp,
          sourceIndex,
          callId: call.id,
        });
        nativeCalls.set(call.id, record);
      }

      return;
    }

    if (message.role === "tool") {
      const id = toolCallId(metadata);
      const output = toolOutput(message, metadata);
      const isError =
        readBoolean(metadata, [
          "is_error",
          "isError",
          "tool_output_is_error",
          "success",
        ]) === false ||
        readBoolean(metadata, ["is_error", "isError", "tool_output_is_error"]);
      const existing = id ? nativeCalls.get(id) : undefined;
      if (existing) {
        existing.output = output;
        existing.isError = isError;
        existing.summary = `${existing.title} → ${previewText(output)}`;
        return;
      }

      const name = asString(metadata.tool_name) ?? "TOOL RESULT";
      add({
        id: `${identity}:tool-result:${id || "unknown"}`,
        kind: "tool",
        turn,
        request: request || null,
        title: name,
        summary: `${name} → ${previewText(output)}`,
        output,
        timestamp,
        sourceIndex,
        callId: id,
        isError,
      });
      return;
    }

    add({
      id: `${identity}:message`,
      kind: "message",
      turn,
      request: request || null,
      title: message.role.toUpperCase() || "MESSAGE",
      summary: firstLine(content),
      output: content,
      timestamp,
      sourceIndex,
    });
  });

  return records;
}
