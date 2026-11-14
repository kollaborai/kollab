import { EngineApi } from "@/api";

/**
 * fetch() for the engine's /sessions/{id}/code/* routes (files, git, terminal), ported from mentiko's
 * Next.js /api/fs/* and /api/git routes. The engine passes each call on to the daemon of that session, so a
 * session opened from another computer shows that computer's files, git and shell. Callers keep mentiko's URLs
 * and response envelope:
 *
 *   codeFetch("/api/fs/tree?workspace=...")  ->  GET {engine}/sessions/{id}/code/fs/tree?workspace=...
 *   codeFetch("/api/git", { method: "POST" })  ->  POST {engine}/sessions/{id}/code/git
 *
 * Success bodies are `{ success: true, data }`; failures are a non-2xx status with
 * `{ success: false, error: { code, message } }`. Unlike fetch(), a non-2xx response does
 * NOT throw here: callers read `res.ok` and `unwrapCodeData(await res.json())` like mentiko.
 */
const api = new EngineApi();

/** Shown in place of the error when a session's daemon predates Code, Git and Terminal. No version or code in it. */
export const OUTDATED_NOTICE = "This agent runs older code. Restart it to use Code, Git and Terminal.";

// The session the open Code Editor view belongs to. One view is shown at a time and it sets this while it renders,
// before any of its children fetch, so the many call sites keep mentiko's single-argument codeFetch.
let activeCodeSession = "";
let activeCodeRoot = "";
export function setCodeRoot(root: string): void { activeCodeRoot = root; }

export function setCodeSession(sessionId: string): void {
  activeCodeSession = sessionId;
}

export function codeBase(sessionId: string = activeCodeSession): string {
  return `/sessions/${encodeURIComponent(sessionId)}/code`;
}

export function codeUrl(url: string, sessionId: string = activeCodeSession): string {
  const base = codeBase(sessionId);
  if (url.startsWith("/api/fs/")) return `${base}/fs/${url.slice("/api/fs/".length)}`;
  if (url === "/api/git" || url.startsWith("/api/git?")) return `${base}/git${url.slice("/api/git".length)}`;
  return url.startsWith("/code/") ? `${base}${url.slice("/code".length)}` : `${base}${url}`;
}

export function codeRequestUrl(url: string, context?: { sessionId: string; root: string }): string {
  const root = context?.root ?? activeCodeRoot;
  let target = codeUrl(url, context?.sessionId);
  if (root && (url.startsWith("/api/fs/") || url === "/api/git" || url.startsWith("/api/git?"))) {
    target += `${target.includes("?") ? "&" : "?"}codeRoot=${encodeURIComponent(root)}`;
  }
  return target;
}

export async function codeFetch(url: string, init: RequestInit = {}, context?: { sessionId: string; root: string }): Promise<Response> {
  try {
    return await api.request(codeRequestUrl(url, context), init);
  } catch (error) {
    // EngineApi.request throws on non-2xx; hand the response shape back so mentiko-style
    // `if (!res.ok)` call sites keep working.
    const status = (error as { status?: number }).status ?? 500;
    const body = (error as { body?: unknown }).body;
    const message = error instanceof Error ? error.message : "request failed";
    const payload =
      body && typeof body === "object" && "success" in (body as object)
        ? body
        : { success: false, error: { code: "request_failed", message } };
    return new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }
}

/** The `data` of a `{ success, data }` envelope; throws the envelope's error message otherwise. */
export function unwrapCodeData<T>(payload: unknown): T {
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    if (record.success === false) {
      const error = record.error as { message?: unknown } | undefined;
      throw new Error(typeof error?.message === "string" ? error.message : "request failed");
    }
    if ("data" in record && record.data !== undefined) return record.data as T;
  }
  return payload as T;
}

/** The engine WebSocket URL for one session's terminal (auth happens in the first message, never in the URL). */
export function codeTerminalUrl(sessionId: string): Promise<string> {
  return api.websocketUrl(`${codeBase(sessionId)}/terminal`);
}

/** The bearer token for the first terminal message. */
export async function codeToken(): Promise<string> {
  await api.loadConfig();
  return api.getToken() ?? "";
}
