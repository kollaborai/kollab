// The frames an engine ends a run's stream with.
const FINISH = new TextEncoder().encode(
  'data: {"type":"message-finish","finishReason":"stop","path":[]}\n\ndata: [DONE]\n\n',
);
const LF = 10;
const CR = 13;
const MAX_EVENT_BYTES = 16 * 1024 * 1024;

type Boundary = { chunk: number; offset: number };

function byteAfter(chunks: readonly Uint8Array[], chunk: number, offset: number): number | null {
  for (let index = chunk; index < chunks.length; index += 1) {
    const nextOffset = index === chunk ? offset + 1 : 0;
    if (nextOffset < chunks[index].length) return chunks[index][nextOffset];
  }
  return null;
}

function endsWithBlankLine(bytes: readonly number[]): boolean {
  const n = bytes.length;
  if (n < 2) return false;
  const last = bytes[n - 1];
  const before = bytes[n - 2];
  return (last === LF && before === LF) ||
    (last === CR && (before === CR || before === LF)) ||
    (last === LF && before === CR && n >= 3 &&
      (bytes[n - 3] === CR || bytes[n - 3] === LF));
}

/** Find the end of the first SSE event, including a separator split by chunks. */
function firstEventBoundary(chunks: readonly Uint8Array[], scanFrom = 0): Boundary | null {
  const previous: number[] = [];
  let absoluteOffset = 0;
  for (let chunk = 0; chunk < chunks.length; chunk += 1) {
    for (let offset = 0; offset < chunks[chunk].length; offset += 1) {
      if (absoluteOffset >= scanFrom) {
        previous.push(chunks[chunk][offset]);
        if (previous.length > 4) previous.shift();
        const n = previous.length;
        const last = previous[n - 1];
        if (endsWithBlankLine(previous)) {
          // Preserve an available LF after CR. If it has not arrived yet,
          // dispatch CR as a valid standalone line ending instead of waiting
          // for another source byte.
          const next = last === CR ? byteAfter(chunks, chunk, offset) : null;
          if (last !== CR || next !== LF) {
            return { chunk, offset };
          }
        }
      }
      absoluteOffset += 1;
    }
  }
  return null;
}

function eventLength(chunks: readonly Uint8Array[], boundary: Boundary): number {
  let length = 0;
  for (let chunk = 0; chunk < boundary.chunk; chunk += 1) length += chunks[chunk].length;
  return length + boundary.offset + 1;
}

function join(chunks: readonly Uint8Array[]): Uint8Array {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const result = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}

function takeEvent(chunks: readonly Uint8Array[], boundary: Boundary) {
  const event: Uint8Array[] = [];
  const remainder: Uint8Array[] = [];
  for (let index = 0; index < chunks.length; index += 1) {
    const chunk = chunks[index];
    if (index < boundary.chunk) event.push(chunk);
    else if (index === boundary.chunk) {
      event.push(chunk.subarray(0, boundary.offset + 1));
      if (boundary.offset + 1 < chunk.length) remainder.push(chunk.subarray(boundary.offset + 1));
    } else remainder.push(chunk);
  }
  return { event: join(event), remainder };
}

/** True when bytes are an incomplete event rather than a queued full event. */
function hasPartialEvent(chunks: readonly Uint8Array[]): boolean {
  const nonEmpty = chunks.filter((chunk) => chunk.length > 0);
  return nonEmpty.length > 0 && firstEventBoundary(nonEmpty) === null;
}

/**
 * A run's event stream that the page can end. Complete SSE events are passed
 * one at a time; a caller can pause between events so its consumer can handle
 * the preceding state before this stream reads another queued event.
 */
export function endableStream(
  source: ReadableStream<Uint8Array>,
  options: { beforeNextEvent?: (event: Uint8Array) => void | Promise<void> } = {},
): {
  stream: ReadableStream<Uint8Array>;
  end: () => void;
} {
  const reader = source.getReader();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  let pending: Uint8Array[] = [];
  let pendingBytes = 0;
  let pendingScanned = 0;
  let sourceDone = false;
  let pauseAfterEvent = false;
  let previousEvent: Uint8Array | null = null;
  const tail: number[] = [];   // the last bytes passed on
  let ending = false;          // finish after the event now arriving
  let closed = false;
  let releasePullWait: (() => void) | null = null;
  const unblockPull = () => {
    releasePullWait?.();
    releasePullWait = null;
  };
  const note = (bytes: Uint8Array) => {
    for (const byte of bytes) {
      tail.push(byte);
      if (tail.length > 4) tail.shift();
    }
  };
  const endsEvent = () => {
    return tail.length === 0 || endsWithBlankLine(tail);
  };
  const finish = () => {
    if (closed) return;
    closed = true;
    unblockPull();
    pending = [];
    pendingBytes = 0;
    controller.enqueue(FINISH);
    controller.close();
    void reader.cancel().catch(() => undefined);
  };
  const close = () => {
    if (closed) return;
    closed = true;
    unblockPull();
    controller.close();
  };
  const fail = async (error: Error) => {
    if (closed) return;
    closed = true;
    unblockPull();
    pending = [];
    pendingBytes = 0;
    controller.error(error);
    await reader.cancel(error).catch(() => undefined);
  };
  const stream = new ReadableStream<Uint8Array>({
    start: (streamController) => { controller = streamController; },
    // A read that fails fails the stream.
    pull: async () => {
      if (closed) return;
      if (pauseAfterEvent) {
        pauseAfterEvent = false;
        const event = previousEvent;
        previousEvent = null;
        const wait = event ? options.beforeNextEvent?.(event) : undefined;
        if (wait) {
          let release!: () => void;
          const closedPromise = new Promise<void>((resolve) => { release = resolve; });
          releasePullWait = release;
          await Promise.race([wait, closedPromise]);
          if (releasePullWait === release) releasePullWait = null;
        }
        if (closed) return;
      }
      while (!closed) {
        const scanFrom = Math.max(0, pendingScanned - 3);
        const boundary = firstEventBoundary(pending, scanFrom);
        if (boundary) {
          const length = eventLength(pending, boundary);
          if (length > MAX_EVENT_BYTES) {
            await fail(new Error(`SSE event exceeds max buffered size (${length} > ${MAX_EVENT_BYTES})`));
            return;
          }
          const next = takeEvent(pending, boundary);
          pending = next.remainder;
          pendingBytes -= length;
          pendingScanned = 0;
          controller.enqueue(next.event);
          note(next.event);
          previousEvent = next.event;
          if (ending) finish();
          else pauseAfterEvent = true;
          return;
        }
        pendingScanned = pendingBytes;
        if (sourceDone) {
          if (pendingBytes > MAX_EVENT_BYTES) {
            await fail(new Error(`SSE event exceeds max buffered size (${pendingBytes} > ${MAX_EVENT_BYTES})`));
            return;
          }
          if (pending.length) {
            const partial = join(pending);
            pending = [];
            pendingBytes = 0;
            controller.enqueue(partial);
            note(partial);
          }
          close();
          return;
        }
        const { done, value } = await reader.read();
        if (closed) return;
        if (done) {
          sourceDone = true;
          continue;
        }
        if (value.length === 0) continue;
        pending.push(value);
        pendingBytes += value.length;
        if (pendingBytes > MAX_EVENT_BYTES && firstEventBoundary(pending, Math.max(0, pendingScanned - 3)) === null) {
          await fail(new Error(`SSE event exceeds max buffered size (${pendingBytes} > ${MAX_EVENT_BYTES})`));
          return;
        }
      }
    },
    cancel: (reason) => {
      closed = true;
      unblockPull();
      return reader.cancel(reason);
    },
  }, { highWaterMark: 0 });
  return {
    stream,
    end: () => {
      if (closed || ending) return;
      ending = true;
      if (endsEvent() && !hasPartialEvent(pending)) finish();
    },
  };
}
