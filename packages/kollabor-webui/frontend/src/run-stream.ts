// The frames an engine ends a run's stream with.
const FINISH = new TextEncoder().encode(
  'data: {"type":"message-finish","finishReason":"stop","path":[]}\n\ndata: [DONE]\n\n',
);
const LF = 10;
const CR = 13;

/** True when the bytes end an event (a blank line), or there are none. */
const endsEvent = (tail: readonly number[]) => {
  const n = tail.length;
  return n === 0 ||
    (tail[n - 1] === LF && tail[n - 2] === LF) ||
    (tail[n - 1] === LF && tail[n - 2] === CR && tail[n - 3] === LF && tail[n - 4] === CR);
};

/**
 * A run's event stream that the page can end. `stream` passes `source` on as
 * it comes. `end()` finishes it as the engine does when a turn ends, after the
 * event now arriving (an event cut short reads as a broken stream), and lets
 * `source` go: whatever the engine sends after that is not read.
 */
export function endableStream(source: ReadableStream<Uint8Array>): {
  stream: ReadableStream<Uint8Array>;
  end: () => void;
} {
  const reader = source.getReader();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const tail: number[] = [];   // the last bytes passed on
  let ending = false;          // the page asked for the end; it comes where the event now arriving ends
  let closed = false;
  const note = (byte: number) => {
    tail.push(byte);
    if (tail.length > 4) tail.shift();
  };
  const finish = () => {
    closed = true;
    controller.enqueue(FINISH);
    controller.close();
    void reader.cancel().catch(() => undefined);
  };
  const stream = new ReadableStream<Uint8Array>({
    start: (streamController) => { controller = streamController; },
    cancel: (reason) => {
      closed = true;
      return reader.cancel(reason);
    },
  });
  void (async () => {
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (closed) return;
        if (done) {
          closed = true;
          controller.close();
          return;
        }
        if (!ending) {
          controller.enqueue(value);
          value.subarray(-4).forEach(note);
          continue;
        }
        const at = value.findIndex((byte) => {
          note(byte);
          return endsEvent(tail);
        });
        if (at < 0) {
          controller.enqueue(value);
          continue;
        }
        controller.enqueue(value.subarray(0, at + 1));
        finish();
        return;
      }
    } catch (error) {
      if (!closed) {
        closed = true;
        controller.error(error);
      }
    }
  })();
  return {
    stream,
    end: () => {
      if (closed || ending) return;
      ending = true;
      if (endsEvent(tail)) finish();
    },
  };
}
