import { useCallback, useEffect, useRef, useState } from "react";

import { codeTerminalUrl, codeToken } from "@/lib/code-fetch";

// One terminal session over the engine's /code/terminal WebSocket. Each tab owns one socket.
// Client sends: auth (always first), open (second), input, resize, close.
// Server sends: ready, output, exit, error.
// "attached" is set only when the server's `ready` arrives, never when the socket merely opens.

export type TerminalWsStatus = "connecting" | "attached" | "closed" | "error";

export interface TerminalWsState {
  status: TerminalWsStatus;
  /** The shell's id, set once `ready` arrives. A reconnect resumes this session. */
  sessionId: string | null;
  /** The server's reason when status is "error"; null otherwise. */
  errorMessage: string | null;
}

export interface TerminalInputMessage {
  type: "input";
  data: string;
}

export interface TerminalWsConnection extends TerminalWsState {
  /** Sends keystrokes. Dropped unless status is "attached". */
  send: (msg: TerminalInputMessage) => void;
  /** Sets the shell size. Sent at once when attached; otherwise it goes out with the next `ready`. */
  resize: (cols: number, rows: number) => void;
  /** Adds an output listener and returns the function that removes it. */
  onOutput: (cb: (data: string) => void) => () => void;
  /** Adds a listener for the shell ending and returns the function that removes it. */
  onExit: (cb: () => void) => () => void;
  /** Ends the shell on the server and closes the socket. */
  close: () => void;
}

export interface UseTerminalWsConnectionOptions {
  /** Folder the shell opens in (the active chat's workspace). Changing it starts a new session. */
  workspace: string;
  /** Size for the first `open`. Later sizes go through `resize`. */
  cols?: number;
  rows?: number;
  /** false disconnects; the shell keeps running on the server. Defaults to true. */
  enabled?: boolean;
}

export interface TerminalWsClientOptions {
  workspace: string;
  cols: number;
  rows: number;
  onState: (state: TerminalWsState) => void;
  onOutput: (data: string) => void;
  onExit: () => void;
}

export interface TerminalWsClient {
  send: (msg: TerminalInputMessage) => void;
  resize: (cols: number, rows: number) => void;
  /** Ends the shell on the server, then closes the socket. */
  close: () => void;
  /** Closes the socket and leaves the shell running; the server ends it once nobody reattaches. */
  dispose: () => void;
}

const AUTH_FAILED_CLOSE_CODE = 4401;
const NORMAL_CLOSE_CODE = 1000;
const MAX_RECONNECT_TRIES = 5;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 4000;
const SOCKET_OPEN = 1;
const DEFAULT_COLS = 80;
const DEFAULT_ROWS = 24;

type ServerMessage =
  | { type: "ready"; sessionId: string }
  | { type: "output"; data: string }
  | { type: "exit" }
  | { type: "error"; message: string };

function parseServerMessage(raw: string): ServerMessage | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  switch (record.type) {
    case "ready":
      return typeof record.sessionId === "string" ? { type: "ready", sessionId: record.sessionId } : null;
    case "output":
      return typeof record.data === "string" ? { type: "output", data: record.data } : null;
    case "exit":
      return { type: "exit" };
    case "error":
      return {
        type: "error",
        message:
          typeof record.message === "string" && record.message
            ? record.message
            : "The terminal server refused the request.",
      };
    default:
      return null;
  }
}

/**
 * The socket and protocol for one terminal session, with no React in it. Starts connecting at once.
 * A dropped socket reconnects with the session it had; a refused open or a rejected token does not.
 */
export function createTerminalWsClient(options: TerminalWsClientOptions): TerminalWsClient {
  const { workspace, onState, onOutput, onExit } = options;
  let size = { cols: options.cols, rows: options.rows };
  let socket: WebSocket | null = null;
  let ready = false;
  let sessionId: string | null = null;
  let attempts = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  // Set by close() or dispose(): nothing connects, reports or forwards after this.
  let ended = false;

  function publish(status: TerminalWsStatus, errorMessage: string | null = null): void {
    onState({ status, sessionId, errorMessage });
  }

  // The socket is no longer this client's: it closed, or this client ended it.
  function release(ws: WebSocket): void {
    if (socket === ws) socket = null;
    ready = false;
  }

  function scheduleReconnect(): void {
    if (ended) return;
    if (attempts >= MAX_RECONNECT_TRIES) {
      sessionId = null;
      publish("error", "Lost the connection to the terminal server.");
      return;
    }
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempts, RECONNECT_MAX_MS);
    attempts += 1;
    publish("connecting");
    timer = setTimeout(() => {
      timer = undefined;
      void connect();
    }, delay);
  }

  async function connect(): Promise<void> {
    const endpoint = await Promise.all([codeTerminalUrl(), codeToken()]).catch(() => null);
    if (ended) return;
    if (!endpoint) {
      scheduleReconnect();
      return;
    }
    const [url, token] = endpoint;
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      scheduleReconnect();
      return;
    }
    socket = ws;

    ws.onopen = () => {
      if (ended || socket !== ws) return;
      // auth is always the first message and open the second. The token never goes in the URL.
      ws.send(JSON.stringify({ type: "auth", token }));
      ws.send(
        JSON.stringify({
          type: "open",
          workspace,
          ...size,
          ...(sessionId ? { sessionId } : {}),
        }),
      );
    };

    ws.onmessage = (event) => {
      if (ended || socket !== ws || typeof event.data !== "string") return;
      const message = parseServerMessage(event.data);
      if (!message) return;
      switch (message.type) {
        case "output":
          onOutput(message.data);
          return;
        case "ready":
          attempts = 0;
          ready = true;
          sessionId = message.sessionId;
          publish("attached");
          // A resize made between `open` and `ready` never reached the server.
          ws.send(JSON.stringify({ type: "resize", ...size }));
          return;
        case "exit":
          // The shell is over: nothing to resume and nothing to reconnect to.
          sessionId = null;
          release(ws);
          publish("closed");
          ws.close(NORMAL_CLOSE_CODE);
          onExit();
          return;
        case "error":
          // Refused input or size while attached: the shell is still there.
          if (ready) return;
          // The server refused the open itself (bad folder, too many terminals, a session id that is gone).
          // It leaves the socket open, so end it here; retrying is the user's call.
          sessionId = null;
          release(ws);
          publish("error", message.message);
          ws.close(NORMAL_CLOSE_CODE);
          return;
      }
    };

    ws.onclose = (event) => {
      if (ended || socket !== ws) return;
      release(ws);
      if (event.code === AUTH_FAILED_CLOSE_CODE) {
        // The same token would be rejected again, so do not retry.
        sessionId = null;
        publish("error", "The engine rejected the terminal token.");
        return;
      }
      scheduleReconnect();
    };
  }

  function end(endSession: boolean): void {
    if (ended) return;
    ended = true;
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    const ws = socket;
    socket = null;
    ready = false;
    if (ws) {
      if (endSession && ws.readyState === SOCKET_OPEN) ws.send(JSON.stringify({ type: "close" }));
      ws.close(NORMAL_CLOSE_CODE);
    }
    sessionId = null;
    publish("closed");
  }

  function send(msg: TerminalInputMessage): void {
    if (!socket || !ready || socket.readyState !== SOCKET_OPEN) return;
    socket.send(JSON.stringify({ type: "input", data: msg.data }));
  }

  function resize(cols: number, rows: number): void {
    size = { cols, rows };
    if (!socket || !ready || socket.readyState !== SOCKET_OPEN) return;
    socket.send(JSON.stringify({ type: "resize", cols, rows }));
  }

  publish("connecting");
  void connect();

  return {
    send,
    resize,
    close: () => end(true),
    dispose: () => end(false),
  };
}

export function useTerminalWsConnection({
  workspace,
  cols = DEFAULT_COLS,
  rows = DEFAULT_ROWS,
  enabled = true,
}: UseTerminalWsConnectionOptions): TerminalWsConnection {
  const [state, setState] = useState<TerminalWsState>(() => ({
    status: enabled ? "connecting" : "closed",
    sessionId: null,
    errorMessage: null,
  }));
  // Listeners live here, not in the client, so a view can subscribe before the socket exists.
  const [outputListeners] = useState(() => new Set<(data: string) => void>());
  const [exitListeners] = useState(() => new Set<() => void>());
  const clientRef = useRef<TerminalWsClient | null>(null);
  // The first open uses the starting size; resize() keeps it current for the next session.
  const sizeRef = useRef({ cols, rows });

  useEffect(() => {
    if (!enabled) return undefined;
    // A new workspace or a re-enable always starts a brand-new session; nothing is resumed.
    const client = createTerminalWsClient({
      workspace,
      cols: sizeRef.current.cols,
      rows: sizeRef.current.rows,
      onState: setState,
      onOutput: (data) => {
        for (const listener of outputListeners) listener(data);
      },
      onExit: () => {
        for (const listener of exitListeners) listener();
      },
    });
    clientRef.current = client;
    return () => {
      if (clientRef.current === client) clientRef.current = null;
      client.dispose();
    };
  }, [enabled, workspace, outputListeners, exitListeners]);

  const send = useCallback((msg: TerminalInputMessage) => {
    clientRef.current?.send(msg);
  }, []);

  const resize = useCallback((nextCols: number, nextRows: number) => {
    sizeRef.current = { cols: nextCols, rows: nextRows };
    clientRef.current?.resize(nextCols, nextRows);
  }, []);

  const onOutput = useCallback(
    (cb: (data: string) => void) => {
      const listener = (data: string) => cb(data);
      outputListeners.add(listener);
      return () => {
        outputListeners.delete(listener);
      };
    },
    [outputListeners],
  );

  const onExit = useCallback(
    (cb: () => void) => {
      const listener = () => cb();
      exitListeners.add(listener);
      return () => {
        exitListeners.delete(listener);
      };
    },
    [exitListeners],
  );

  const close = useCallback(() => {
    clientRef.current?.close();
  }, []);

  return { ...state, send, resize, onOutput, onExit, close };
}
