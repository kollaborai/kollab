import assert from "node:assert/strict";
import { test } from "node:test";

import { endableStream } from "../src/run-stream.ts";

const FINISH = 'data: {"type":"message-finish","finishReason":"stop","path":[]}\n\ndata: [DONE]\n\n';

/** An engine's stream the test feeds by hand, and whether the page let it go. */
function engine() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const state = { let: false };
  const source = new ReadableStream<Uint8Array>({
    start: (streamController) => { controller = streamController; },
    cancel: () => { state.let = true; },
  });
  const encoder = new TextEncoder();
  return {
    source,
    state,
    send: (text: string) => controller.enqueue(encoder.encode(text)),
    close: () => controller.close(),
  };
}

/** Everything `stream` passes on until it ends. */
async function read(stream: ReadableStream<Uint8Array>): Promise<string> {
  const decoder = new TextDecoder();
  let text = "";
  for await (const chunk of stream as unknown as AsyncIterable<Uint8Array>) text += decoder.decode(chunk, { stream: true });
  return text;
}

test("a run's stream is passed on as it comes and ends when the engine ends it", async () => {
  const from = engine();
  const run = endableStream(from.source);
  from.send("data: 1\n\n");
  from.send("data: 2\n\ndata: [DONE]\n\n");
  from.close();
  assert.equal(await read(run.stream), "data: 1\n\ndata: 2\n\ndata: [DONE]\n\n");
  assert.equal(from.state.let, false);
});

test("the page ends a run's stream between two events as the engine ends a turn", async () => {
  const from = engine();
  const run = endableStream(from.source);
  from.send("data: 1\n\n");
  const text = read(run.stream);
  await new Promise((resolve) => setTimeout(resolve, 0));
  run.end();
  run.end();
  assert.equal(await text, `data: 1\n\n${FINISH}`);
  assert.equal(from.state.let, true, "the engine's stream is let go");
});

test("a run's stream ended inside an event ends after that event", async () => {
  const from = engine();
  const run = endableStream(from.source);
  from.send("data: 1\n\ndata: {\"half\":");
  const text = read(run.stream);
  await new Promise((resolve) => setTimeout(resolve, 0));
  run.end();
  from.send("true");
  from.send("}\r\n\r\ndata: 3\n\n");
  assert.equal(await text, `data: 1\n\ndata: {"half":true}\r\n\r\n${FINISH}`);
  assert.equal(from.state.let, true);
});

test("a run's stream ended inside an event passes on nothing the engine sent after that event", async () => {
  // The engine's next event is already waiting when the page ends the stream.
  const encoder = new TextEncoder();
  let letGo = false;
  const source = new ReadableStream<Uint8Array>({
    start: (controller) => {
      for (const chunk of ["data: one\r", "\n\r\n", "data: two\n\n"]) controller.enqueue(encoder.encode(chunk));
    },
    cancel: () => { letGo = true; },
  });
  const run = endableStream(source);
  const reader = run.stream.getReader();
  const decoder = new TextDecoder();
  let text = decoder.decode((await reader.read()).value, { stream: true });
  // Time for a stream that reads ahead to take what waits.
  await new Promise((resolve) => setTimeout(resolve, 0));
  run.end();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    text += decoder.decode(value, { stream: true });
  }
  assert.equal(text, `data: one\r\n\r\n${FINISH}`);
  assert.equal(letGo, true, "the engine's stream is let go");
});

test("a run's stream ended after one event holds back the next queued event", async () => {
  const encoder = new TextEncoder();
  const source = new ReadableStream<Uint8Array>({
    start: (controller) => {
      controller.enqueue(encoder.encode("data: one\n\n"));
      controller.enqueue(encoder.encode("data: two\n\n"));
      controller.close();
    },
  });
  const run = endableStream(source);
  // The assistant-transport reader pipes the response through transforms,
  // which can have the next read pending while the page handles this event.
  const consumed = run.stream.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform: (chunk, controller) => controller.enqueue(chunk),
  }));
  const reader = consumed.getReader();
  const decoder = new TextDecoder();
  let text = decoder.decode((await reader.read()).value);
  await new Promise((resolve) => setTimeout(resolve, 0));
  run.end();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    text += decoder.decode(value, { stream: true });
  }
  assert.equal(text, `data: one\n\n${FINISH}`);
});

test("a run's stream ended before its first event holds only the end", async () => {
  const from = engine();
  const run = endableStream(from.source);
  run.end();
  assert.equal(await read(run.stream), FINISH);
});

test("a reader that lets a run's stream go lets the engine's stream go", async () => {
  const from = engine();
  const run = endableStream(from.source);
  await run.stream.cancel();
  run.end();
  assert.equal(from.state.let, true);
});
