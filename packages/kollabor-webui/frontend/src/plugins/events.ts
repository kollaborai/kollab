type Listener = (data: unknown) => void;
type Request = (path: string, init?: RequestInit) => Promise<Response>;

/** Authenticated SSE; shared by the inventory and each plugin's scoped stream. */
export class PluginEventStream {
  private readonly listeners = new Map<string, Set<Listener>>();
  private controller: AbortController | null = null;
  private closed = false;

  constructor(
    private readonly request: Request,
    private readonly path: string,
    private readonly onError: (error: unknown) => void = error => console.warn("Plugin event stream disconnected", error),
    private readonly onReconnect: () => void = () => {},
  ) {}

  on(topic: string, listener: Listener): () => void {
    if (this.closed) return () => {};
    const listeners = this.listeners.get(topic) ?? new Set<Listener>();
    listeners.add(listener);
    this.listeners.set(topic, listeners);
    if (!this.controller) {
      const controller = new AbortController();
      this.controller = controller;
      void this.run(controller);
    }
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      listeners.delete(listener);
      if (!listeners.size) this.listeners.delete(topic);
      if (!this.listeners.size) this.stop();
    };
  }

  close() { this.closed = true; this.listeners.clear(); this.stop(); }

  private stop() {
    const controller = this.controller;
    this.controller = null;
    controller?.abort();
  }

  private async run(controller: AbortController) {
    const { signal } = controller;
    let delay = 250;
    let connected = false;
    while (!signal.aborted) {
      let receivedData = false;
      try {
        const response = await this.request(this.path, { method: "GET", signal });
        if (signal.aborted) { await response.body?.cancel(); break; }
        if (!response.ok) {
          await response.body?.cancel();
          throw new Error(`Plugin event stream returned ${response.status}`);
        }
        if (!response.body) throw new Error("Plugin event stream has no body");
        if (connected) this.onReconnect();
        connected = true;
        await this.read(response.body, signal, () => { receivedData = true; });
      } catch (error) {
        if (!signal.aborted) this.onError(error);
      }
      if (!signal.aborted) {
        if (receivedData) delay = 250;
        await new Promise<void>(resolve => {
          const timer = setTimeout(done, delay);
          function done() { clearTimeout(timer); signal.removeEventListener("abort", done); resolve(); }
          signal.addEventListener("abort", done, { once: true });
        });
        delay = Math.min(delay * 2, 5000);
      }
    }
  }

  private async read(body: ReadableStream<Uint8Array>, signal: AbortSignal, onData: () => void) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const cancel = () => { void reader.cancel().catch(() => {}); };
    signal.addEventListener("abort", cancel, { once: true });
    try {
      while (!signal.aborted) {
        const { value, done } = await reader.read();
        if (done) break;
        if (value?.byteLength) onData();
        buffer += decoder.decode(value, { stream: true });
        const delimiter = /\r?\n\r?\n/g;
        let consumed = 0;
        let match: RegExpExecArray | null;
        while ((match = delimiter.exec(buffer)) !== null) {
          if (match.index - consumed > 1_000_000) throw new Error("Plugin event frame is too large");
          this.dispatch(buffer.slice(consumed, match.index));
          consumed = delimiter.lastIndex;
        }
        buffer = buffer.slice(consumed);
        if (buffer.length > 1_000_000) throw new Error("Plugin event frame is too large");
      }
    } finally {
      signal.removeEventListener("abort", cancel);
      // Close the response body on parser errors as well as normal disconnects.
      // Do not wait for an underlying source's cancellation promise to settle.
      void reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }

  private dispatch(frame: string) {
    const data = frame.split(/\r?\n/).filter(line => line.startsWith("data:"))
      .map(line => line.slice(5).trimStart()).join("\n");
    if (!data) return;
    let event: { topic?: unknown; data?: unknown };
    try { event = JSON.parse(data) as typeof event; } catch { return; }
    if (typeof event.topic !== "string") return;
    for (const listener of this.listeners.get(event.topic) ?? []) {
      try { listener(event.data); } catch (error) { this.onError(error); }
    }
  }
}
