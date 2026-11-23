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
  let releaseNextEvent!: () => void;
  let enteredNextEvent!: () => void;
  const nextEventGate = new Promise<void>((resolve) => { releaseNextEvent = resolve; });
  const nextEventStarted = new Promise<void>((resolve) => { enteredNextEvent = resolve; });
  const run = endableStream(source, { beforeNextEvent: () => {
    enteredNextEvent();
    return nextEventGate;
  } });
  // The assistant-transport reader pipes the response through transforms,
  // which can have the next read pending while the page handles this event.
  const consumed = run.stream.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform: (chunk, controller) => controller.enqueue(chunk),
  }));
  const reader = consumed.getReader();
  const decoder = new TextDecoder();
  let text = decoder.decode((await reader.read()).value);
  const nextRead = reader.read();
  await nextEventStarted;
  run.end();
  releaseNextEvent();
  let next = await nextRead;
  while (!next.done) {
    text += decoder.decode(next.value, { stream: true });
    next = await reader.read();
  }
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    text += decoder.decode(value, { stream: true });
  }
  assert.equal(text, `data: one\n\n${FINISH}`);
});

test("a handover gate splits coalesced SSE events before reading the next one", async () => {
  const encoder = new TextEncoder();
  const source = new ReadableStream<Uint8Array>({
    start: (controller) => {
      controller.enqueue(encoder.encode("data: one\n\ndata: two\r\n\r\n"));
      controller.close();
    },
  });
  let releaseNextEvent!: () => void;
  let enteredNextEvent!: () => void;
  const nextEventGate = new Promise<void>((resolve) => { releaseNextEvent = resolve; });
  const nextEventStarted = new Promise<void>((resolve) => { enteredNextEvent = resolve; });
  const run = endableStream(source, { beforeNextEvent: () => {
    enteredNextEvent();
    return nextEventGate;
  } });
  const reader = run.stream.getReader();
  const decoder = new TextDecoder();
  let text = decoder.decode((await reader.read()).value);
  const nextRead = reader.read();
  await nextEventStarted;
  run.end();
  releaseNextEvent();
  const next = await nextRead;
  if (!next.done) text += decoder.decode(next.value, { stream: true });
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

test("an engine read error still fails the run stream", async () => {
  const encoder = new TextEncoder();
  let sent = false;
  const source = new ReadableStream<Uint8Array>({
    pull: (controller) => {
      if (!sent) {
        sent = true;
        controller.enqueue(encoder.encode("data: one\n\n"));
      } else {
        controller.error(new Error("engine stream failed"));
      }
    },
  });
  const run = endableStream(source);
  const reader = run.stream.getReader();
  const first = await reader.read();
  assert.equal(new TextDecoder().decode(first.value), "data: one\n\n");
  await assert.rejects(reader.read(), /engine stream failed/);
});

test("mixed LF and CRLF line endings still delimit separate SSE events", async () => {
  const first = "data: first\n\r\n";
  const second = "data: second\r\n\r\n";
  const source = new ReadableStream<Uint8Array>({
    start: (controller) => {
      controller.enqueue(new TextEncoder().encode(first + second));
      controller.close();
    },
  });
  const reader = endableStream(source).stream.getReader();

  assert.equal(new TextDecoder().decode((await reader.read()).value), first);
  await reader.cancel();
});

test("end finishes after a complete event followed by an empty source chunk", async () => {
  const first = "data: first\n\n";
  let sourceController!: ReadableStreamDefaultController<Uint8Array>;
  const source = new ReadableStream<Uint8Array>({
    start: (controller) => {
      sourceController = controller;
      controller.enqueue(new TextEncoder().encode(first));
      controller.enqueue(new Uint8Array());
    },
  });
  const run = endableStream(source);
  const reader = run.stream.getReader();
  assert.equal(new TextDecoder().decode((await reader.read()).value), first);

  const pending = reader.read();
  await new Promise((resolve) => setTimeout(resolve, 0));
  run.end();
  const result = await Promise.race([
    pending.then(({ done, value }) => ({
      kind: done ? "done" : "value",
      text: value ? new TextDecoder().decode(value) : "",
    })),
    new Promise<{ kind: string; text: string }>((resolve) =>
      setTimeout(() => resolve({ kind: "stalled", text: "" }), 40),
    ),
  ]);
  if (result.kind === "stalled") sourceController.close();
  await reader.cancel().catch(() => undefined);

  assert.deepEqual(result, { kind: "value", text: FINISH });
});

test("an oversized unterminated SSE line fails within the decoder's bound", async () => {
  const limit = 16 * 1024 * 1024;
  const bytes = new Uint8Array(limit + 1);
  bytes.set(new TextEncoder().encode("data: "));
  let cancelled = false;
  const source = new ReadableStream<Uint8Array>({
    start: (controller) => controller.enqueue(bytes),
    cancel: () => { cancelled = true; },
  });
  const reader = endableStream(source).stream.getReader();
  const result = await Promise.race([
    reader.read().then(
      ({ done }) => ({ kind: done ? "done" : "value" }),
      (error: Error) => ({ kind: "error", message: error.message }),
    ),
    new Promise<{ kind: string }>((resolve) =>
      setTimeout(() => resolve({ kind: "stalled" }), 1500),
    ),
  ]);
  await reader.cancel().catch(() => undefined);

  assert.equal(result.kind, "error", "the wrapper rejects instead of waiting for a delimiter");
  assert.match("message" in result ? result.message : "", /SSE event exceeds/);
  assert.equal(cancelled, true, "the oversized source is cancelled");
});
