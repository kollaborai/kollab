import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  createTerminalWsClient,
  type TerminalWsState,
} from "../src/components/terminal/use-terminal-ws-connection";

vi.mock("@/lib/code-fetch", () => ({
  codeTerminalUrl: async (sessionId: string) => `ws://engine.test/sessions/${sessionId}/code/terminal`,
  codeToken: async () => "token-123",
}));

/** A WebSocket stand-in. The test plays the server: serverOpen, serverSend, serverClose. */
class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static sockets: FakeWebSocket[] = [];

  readonly url: string;
  readyState = FakeWebSocket.CONNECTING;
  readonly sent: string[] = [];
  closedWith: number | null = null;
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.sockets.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(code?: number): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.closedWith = code ?? null;
  }

  serverOpen(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.({});
  }

  serverSend(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  serverClose(code: number): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code });
  }
}

function sentTypes(socket: FakeWebSocket): string[] {
  return socket.sent.map((raw) => (JSON.parse(raw) as { type: string }).type);
}

function lastSocket(): FakeWebSocket {
  const socket = FakeWebSocket.sockets.at(-1);
  if (!socket) throw new Error("no socket was opened");
  return socket;
}

/** connect() awaits the mocked URL and token before it opens a socket. */
async function settle(): Promise<void> {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
}

function start() {
  const states: TerminalWsState[] = [];
  const outputs: string[] = [];
  const client = createTerminalWsClient({
    engineSessionId: "sess-a",
    cols: 80,
    rows: 24,
    onState: (state) => states.push(state),
    onOutput: (data) => outputs.push(data),
    onExit: () => undefined,
  });
  return {
    client,
    outputs,
    state: (): TerminalWsState => states[states.length - 1],
  };
}

beforeEach(() => {
  FakeWebSocket.sockets = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("auth is the first message and open is the second", async () => {
  start();
  await settle();
  const socket = lastSocket();
  expect(socket.url).toBe("ws://engine.test/sessions/sess-a/code/terminal");
  expect(socket.sent).toEqual([]);
  socket.serverOpen();
  expect(socket.sent.map((raw) => JSON.parse(raw) as unknown)).toEqual([
    { type: "auth", token: "token-123" },
    { type: "open", cols: 80, rows: 24 },
  ]);
});

test("status stays connecting until the server sends ready", async () => {
  const { state, outputs } = start();
  await settle();
  const socket = lastSocket();
  socket.serverOpen();
  expect(state().status).toBe("connecting");
  expect(state().sessionId).toBeNull();
  socket.serverSend({ type: "ready", sessionId: "sess-1" });
  expect(state()).toEqual({ status: "attached", sessionId: "sess-1", errorMessage: null });
  socket.serverSend({ type: "output", data: "$ " });
  expect(outputs).toEqual(["$ "]);
});

test("close code 4401 ends in error and never reconnects", async () => {
  const { state } = start();
  await settle();
  const socket = lastSocket();
  socket.serverOpen();
  socket.serverClose(4401);
  expect(state().status).toBe("error");
  await vi.advanceTimersByTimeAsync(60_000);
  await settle();
  expect(FakeWebSocket.sockets).toHaveLength(1);
});

test("a dropped socket reconnects after 1s and resumes the saved session", async () => {
  const { state } = start();
  await settle();
  const first = lastSocket();
  first.serverOpen();
  first.serverSend({ type: "ready", sessionId: "sess-1" });
  first.serverClose(1006);
  expect(state().status).toBe("connecting");
  await vi.advanceTimersByTimeAsync(999);
  expect(FakeWebSocket.sockets).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1);
  await settle();
  const second = lastSocket();
  expect(second).not.toBe(first);
  second.serverOpen();
  expect(sentTypes(second)).toEqual(["auth", "open"]);
  expect(JSON.parse(second.sent[1]) as unknown).toMatchObject({ type: "open", sessionId: "sess-1" });
});

test("close ends the shell on the server; dispose only drops the socket", async () => {
  const ended = start();
  await settle();
  const endedSocket = lastSocket();
  endedSocket.serverOpen();
  endedSocket.serverSend({ type: "ready", sessionId: "sess-a" });
  ended.client.close();
  expect(sentTypes(endedSocket).at(-1)).toBe("close");
  expect(endedSocket.closedWith).toBe(1000);
  expect(ended.state().status).toBe("closed");

  const kept = start();
  await settle();
  const keptSocket = lastSocket();
  keptSocket.serverOpen();
  keptSocket.serverSend({ type: "ready", sessionId: "sess-b" });
  kept.client.dispose();
  expect(sentTypes(keptSocket)).not.toContain("close");
  expect(keptSocket.closedWith).toBe(1000);
});
