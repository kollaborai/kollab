// The frames an engine ends a run's stream with.
const FINISH = new TextEncoder().encode(
  'data: {"type":"message-finish","finishReason":"stop","path":[]}\n\ndata: [DONE]\n\n',
);
const LF = 10;
const CR = 13;

type Boundary = { chunk: number; offset: number };

/** Find the end of the first SSE event, including a separator split by chunks. */
function firstEventBoundary(chunks: readonly Uint8Array[]): Boundary | null {
  const previous: number[] = [];
  for (let chunk = 0; chunk < chunks.length; chunk += 1) {
    for (let offset = 0; offset < chunks[chunk].length; offset += 1) {
      previous.push(chunks[chunk][offset]);
      if (previous.length > 4) previous.shift();
      const n = previous.length;
      if (n >= 2 && previous[n - 1] === LF && previous[n - 2] === LF) {
        return { chunk, offset };
      }
      if (n >= 2 && previous[n - 1] === CR && previous[n - 2] === CR) {
        return { chunk, offset };
      }
      if (n >= 4 && previous[n - 1] === LF && previous[n - 2] === CR &&
          previous[n - 3] === LF && previous[n - 4] === CR) {
        return { chunk, offset };
      }
    }
  }
  return null;
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
  return chunks.length > 0 && firstEventBoundary(chunks) === null;
}

/**
 * A run's event stream that the page can end. Complete SSE events are passed
 * one at a time; a caller can pause between events so its consumer can handle
 * the preceding state before this stream reads another queued event.
 */
export function endableStream(
  source: ReadableStream<Uint8Array>,
  options: { beforeNextEvent?: () => void | Promise<void> } = {},
): {
  stream: ReadableStream<Uint8Array>;
  end: () => void;
} {
  const reader = source.getReader();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  let pending: Uint8Array[] = [];
  let sourceDone = false;
  let pauseAfterEvent = false;
  const tail: number[] = [];   // the last bytes passed on
  let ending = false;          // finish after the event now arriving
  let closed = false;
  const note = (bytes: Uint8Array) => {
    for (const byte of bytes) {
      tail.push(byte);
      if (tail.length > 4) tail.shift();
    }
  };
  const endsEvent = () => {
    const n = tail.length;
    return n === 0 ||
      (tail[n - 1] === LF && tail[n - 2] === LF) ||
      (tail[n - 1] === CR && tail[n - 2] === CR) ||
      (tail[n - 1] === LF && tail[n - 2] === CR && tail[n - 3] === LF && tail[n - 4] === CR);
  };
  const finish = () => {
    if (closed) return;
    closed = true;
    pending = [];
    controller.enqueue(FINISH);
    controller.close();
    void reader.cancel().catch(() => undefined);
  };
  const close = () => {
    if (closed) return;
    closed = true;
    controller.close();
  };
  const stream = new ReadableStream<Uint8Array>({
    start: (streamController) => { controller = streamController; },
    // A read that fails fails the stream.
    pull: async () => {
      if (closed) return;
      if (pauseAfterEvent) {
        pauseAfterEvent = false;
        const wait = options.beforeNextEvent?.();
        if (wait) await wait;
        if (closed) return;
      }
      while (!closed) {
        const boundary = firstEventBoundary(pending);
        if (boundary) {
          const next = takeEvent(pending, boundary);
          pending = next.remainder;
          controller.enqueue(next.event);
          note(next.event);
          if (ending) finish();
          else pauseAfterEvent = true;
          return;
        }
        if (sourceDone) {
          if (pending.length) {
            const partial = join(pending);
            pending = [];
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
        pending.push(value);
      }
    },
    cancel: (reason) => {
      closed = true;
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
