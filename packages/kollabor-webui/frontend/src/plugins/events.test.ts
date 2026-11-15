import { describe, expect, it, vi } from "vitest";
import { PluginEventStream } from "./events";

describe("plugin event stream", () => {
  it("uses an authenticated request, dispatches only the subscribed topic, and aborts on last unsubscribe", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    let signal: AbortSignal | null | undefined;
    const request = vi.fn(async (_path: string, init?: RequestInit) => {
      signal = init?.signal;
      return new Response(new ReadableStream<Uint8Array>({ start(c) { controller = c; } }));
    });
    const stream = new PluginEventStream(request, "/plugins/example/events");
    const listener = vi.fn();
    const off = stream.on("chain", listener);
    await vi.waitFor(() => expect(request).toHaveBeenCalledOnce());
    expect(request.mock.calls[0][0]).toBe("/plugins/example/events");
    const encode = new TextEncoder();
    controller.enqueue(encode.encode('data: {"topic":"other","data":0}\n\ndata: {"topic":"cha'));
    controller.enqueue(encode.encode('in","data":{"count":3},"ts":1}\n\n'));
    await vi.waitFor(() => expect(listener).toHaveBeenCalledWith({ count: 3 }));
    expect(listener).toHaveBeenCalledTimes(1);
    off();
    expect(signal?.aborted).toBe(true);
    stream.close();
  });

  it("stops all subscriptions when closed", async () => {
    let signal: AbortSignal | null | undefined;
    const request = vi.fn(async (_path: string, init?: RequestInit) => {
      signal = init?.signal;
      return new Response(new ReadableStream<Uint8Array>());
    });
    const stream = new PluginEventStream(request, "/plugins/events");
    stream.on("plugins-changed", vi.fn());
    await vi.waitFor(() => expect(request).toHaveBeenCalledOnce());
    stream.close();
    expect(signal?.aborted).toBe(true);
  });
});
