import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  createTerminalWsClient,
  parseServerMessage,
  type TerminalWsDetail,
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
  const details: TerminalWsDetail[] = [];
  const outputs: string[] = [];
  const client = createTerminalWsClient({
    engineSessionId: "sess-a",
    cols: 80,
    rows: 24,
    onState: (state, detail) => {
      states.push(state);
      details.push(detail);
    },
    onOutput: (data) => outputs.push(data),
    onExit: () => undefined,
  });
  return {
    client,
    outputs,
    state: (): TerminalWsState => states[states.length - 1],
    detail: (): TerminalWsDetail => details[details.length - 1],
  };
}

const OUTDATED = "This agent runs older code. Restart it to use Code, Git and Terminal.";
const REFUSAL = "Read only is on for this agent. Setting: kollabor.code.read_only";

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

test("reconnect resumes after the last displayed output and reports a gap", async () => {
  const { outputs } = start();
  await settle();
  const first = lastSocket();
  first.serverOpen();
  first.serverSend({ type: "ready", sessionId: "sess-1" });
  first.serverSend({ type: "output", data: "old", seq: 0 });
  first.serverClose(1006);
  await vi.advanceTimersByTimeAsync(1000);
  await settle();
  const second = lastSocket();
  second.serverOpen();
  expect(JSON.parse(second.sent[1]) as unknown).toMatchObject({ sessionId: "sess-1", cursor: 1 });
  second.serverSend({ type: "ready", sessionId: "sess-1" });
  second.serverSend({ type: "output", data: "old", seq: 0 });
  second.serverSend({ type: "gap", nextSeq: 3 });
  second.serverSend({ type: "output", data: "new", seq: 3 });
  expect(outputs).toEqual(["old", "\r\n[Terminal output was truncated before it could be displayed.]\r\n", "new"]);
});

test("reconnect before any output still requests sequence zero", async () => {
  const { outputs } = start();
  await settle();
  const first = lastSocket();
  first.serverOpen();
  first.serverSend({ type: "ready", sessionId: "sess-1" });
  first.serverClose(1006);
  await vi.advanceTimersByTimeAsync(1000);
  await settle();
  const second = lastSocket();
  second.serverOpen();
  expect(JSON.parse(second.sent[1]) as unknown).toMatchObject({ sessionId: "sess-1", cursor: 0 });
  second.serverSend({ type: "ready", sessionId: "sess-1" });
  second.serverSend({ type: "gap", nextSeq: 2 });
  expect(outputs).toEqual(["\r\n[Terminal output was truncated before it could be displayed.]\r\n"]);
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

test("parseServerMessage keeps an error's code and sets it to null when there is none", () => {
  expect(parseServerMessage(JSON.stringify({ type: "error", message: OUTDATED, code: "daemon_outdated" }))).toEqual({
    type: "error",
    message: OUTDATED,
    code: "daemon_outdated",
  });
  expect(parseServerMessage(JSON.stringify({ type: "error", message: REFUSAL }))).toEqual({
    type: "error",
    message: REFUSAL,
    code: null,
  });
  expect(parseServerMessage(JSON.stringify({ type: "error", message: "x", code: 7 }))).toEqual({
    type: "error",
    message: "x",
    code: null,
  });
  expect(parseServerMessage(JSON.stringify({ type: "error", message: "" }))).toEqual({
    type: "error",
    message: "The terminal server refused the request.",
    code: null,
  });
});

test("an old daemon's refused open reports daemon_outdated and never reconnects", async () => {
  const { state, detail } = start();
  await settle();
  const socket = lastSocket();
  socket.serverOpen();
  socket.serverSend({ type: "error", message: OUTDATED, code: "daemon_outdated" });
  expect(state()).toEqual({ status: "error", sessionId: null, errorMessage: OUTDATED });
  expect(detail()).toEqual({ errorCode: "daemon_outdated", reconnecting: false, refusal: null });
  expect(socket.closedWith).toBe(1000);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(FakeWebSocket.sockets).toHaveLength(1);
});

test("reconnecting is on from a dropped socket's retry until ready arrives again", async () => {
  const { detail } = start();
  await settle();
  const first = lastSocket();
  first.serverOpen();
  first.serverSend({ type: "ready", sessionId: "sess-1" });
  expect(detail().reconnecting).toBe(false);
  first.serverClose(1006);
  expect(detail().reconnecting).toBe(true);
  await vi.advanceTimersByTimeAsync(1000);
  await settle();
  const second = lastSocket();
  second.serverOpen();
  expect(detail().reconnecting).toBe(true);
  second.serverSend({ type: "ready", sessionId: "sess-1" });
  expect(detail()).toEqual({ errorCode: null, reconnecting: false, refusal: null });
});

test("reconnecting turns off when the retries run out", async () => {
  const { state, detail } = start();
  await settle();
  const first = lastSocket();
  first.serverOpen();
  first.serverSend({ type: "ready", sessionId: "sess-1" });
  first.serverClose(1006);
  for (let i = 0; i < 10 && state().status !== "error"; i += 1) {
    await vi.advanceTimersByTimeAsync(4000);
    await settle();
    if (state().status === "error") break;
    lastSocket().serverClose(1006);
  }
  expect(state().status).toBe("error");
  expect(state().errorMessage).toBe("Lost the connection to the terminal server.");
  expect(detail()).toEqual({ errorCode: null, reconnecting: false, refusal: null });
});

test("a refusal while attached keeps the status and is cleared by the next ready", async () => {
  const { state, detail } = start();
  await settle();
  const socket = lastSocket();
  socket.serverOpen();
  socket.serverSend({ type: "ready", sessionId: "sess-1" });
  socket.serverSend({ type: "error", message: REFUSAL });
  expect(state().status).toBe("attached");
  expect(detail().refusal).toBe(REFUSAL);
  expect(socket.closedWith).toBeNull();
  socket.serverClose(1006);
  await vi.advanceTimersByTimeAsync(1000);
  await settle();
  const second = lastSocket();
  second.serverOpen();
  second.serverSend({ type: "ready", sessionId: "sess-1" });
  expect(detail().refusal).toBeNull();
});
