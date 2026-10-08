import type { MessageTiming } from "@assistant-ui/react";

/**
 * One user turn's clock. A turn can span several transport runs (a permission
 * prompt ends the run and the answer starts the next), so `elapsed` sums the
 * finished runs and leaves out the wait for the human.
 */
export type TurnClock = {
  startedAt: number;
  elapsed: number;
  firstTokenAt?: number;
  chunks: number;
  toolCalls: number;
};

/**
 * Timing of finished turns, keyed by the engine's assistant message id
 * (`assistant-<session>-<n>`, unique across sessions). The runtime converter
 * reads it into each message's `metadata.timing`.
 * ponytail: one small entry per turn, never trimmed; cap it if a tab ever
 * lives for a six-figure number of turns.
 */
export const turnTimings = new Map<string, MessageTiming>();

export const newTurnClock = (): TurnClock => ({
  startedAt: Date.now(),
  elapsed: 0,
  chunks: 0,
  toolCalls: 0,
});

type Frame = {
  type?: string;
  path?: unknown;
  part?: { type?: string; toolName?: string };
  operations?: unknown;
  usage?: { outputTokens?: number };
};

const pathKey = (path: unknown) => (Array.isArray(path) ? path.join(".") : "");

// The engine publishes the finished turn as a `set messages` state patch; its
// last assistant message is the one the thread will render for this turn.
function lastAssistantId(operations: unknown): string | undefined {
  if (!Array.isArray(operations)) return undefined;
  for (const operation of operations as Array<Record<string, unknown>>) {
    const { type, path, value } = operation;
    if (type !== "set" || !Array.isArray(value)) continue;
    if (!Array.isArray(path) || path.length !== 1 || path[0] !== "messages") {
      continue;
    }
    for (let index = value.length - 1; index >= 0; index -= 1) {
      const message = value[index] as { id?: unknown; role?: unknown };
      if (message?.role === "assistant" && typeof message.id === "string") {
        return message.id;
      }
    }
  }
  return undefined;
}

function parseFrame(raw: string): Frame | null {
  const data = raw
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data || data === "[DONE]") return null;
  try {
    return JSON.parse(data) as Frame;
  } catch {
    return null;
  }
}

/**
 * Read one assistant-transport run off a cloned response and record the turn's
 * timing in `turnTimings` when the run ends with `message-finish`. The
 * transport runtime drops every text delta (the thread only renders
 * `state.messages`, written once at turn end), so the wire frames are the only
 * place the stream's real timing exists. Cancelled or failed runs never
 * finish, so they record nothing.
 */
export async function observeRun(
  response: Response,
  turn: TurnClock,
  runStart: number,
): Promise<void> {
  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  const partKinds = new Map<string, string | undefined>();
  let buffer = "";
  let endedAt = runStart;
  let finished = false;
  // The final model call starts when the last tool result lands, so its
  // tokens/sec is measured from `modelStart`, not from the turn start.
  let modelStart = runStart;
  let messageId: string | undefined;

  // Recorded the moment `message-finish` is read, before the transport has
  // finished the stream: its re-render at the end of the run (isRunning going
  // false) then converts the message with the timing already in place.
  const finish = (now: number, outputTokens: number) => {
    finished = true;
    if (!messageId) return;
    // A slash command or hub reply is one chunk and no model tokens; it has no
    // model speed to show.
    if (outputTokens === 0 && turn.chunks < 2) return;
    const modelMs = now - modelStart;
    turnTimings.set(messageId, {
      streamStartTime: turn.startedAt,
      totalStreamTime: turn.elapsed + (now - runStart),
      totalChunks: turn.chunks,
      toolCallCount: turn.toolCalls,
      ...(turn.firstTokenAt !== undefined && {
        firstTokenTime: turn.firstTokenAt,
      }),
      ...(outputTokens > 0 && { tokenCount: outputTokens }),
      ...(outputTokens > 0 &&
        modelMs > 0 && { tokensPerSecond: outputTokens / (modelMs / 1000) }),
    });
  };

  const onFrame = (frame: Frame, now: number) => {
    switch (frame.type) {
      case "part-start": {
        partKinds.set(pathKey(frame.path), frame.part?.type);
        if (
          frame.part?.type === "tool-call" &&
          frame.part.toolName !== "request_permission"
        ) {
          turn.toolCalls += 1;
        }
        break;
      }
      case "text-delta": {
        const kind = partKinds.get(pathKey(frame.path));
        if (kind === "text" || kind === "reasoning") {
          turn.chunks += 1;
          turn.firstTokenAt ??= turn.elapsed + (now - runStart);
        }
        break;
      }
      case "result":
        modelStart = now;
        break;
      case "update-state":
        messageId = lastAssistantId(frame.operations) ?? messageId;
        break;
      case "message-finish":
        finish(now, Number(frame.usage?.outputTokens) || 0);
        break;
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    const now = performance.now();
    if (done) {
      endedAt = now;
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = parseFrame(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (frame) onFrame(frame, now);
      boundary = buffer.indexOf("\n\n");
    }
  }

  // A permission pause or a failed run: the turn goes on (or never records).
  if (!finished) turn.elapsed += endedAt - runStart;
}

/**
 * A finished turn's timing rebuilt from history timestamps, so the reply time
 * survives a reload (`turnTimings` lives in memory). Wall clock from the
 * user's message to the turn's final reply: unlike the live clock it includes
 * any wait on a permission prompt.
 */
export const historyTurnTiming = (
  startedAt: string | null | undefined,
  endedAt: string | null | undefined,
  toolCallCount: number,
): MessageTiming | undefined => {
  const start = Date.parse(startedAt ?? "");
  const end = Date.parse(endedAt ?? "");
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return undefined;
  return { streamStartTime: start, totalStreamTime: end - start, totalChunks: 0, toolCallCount };
};
