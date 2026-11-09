import { EngineApi } from "@/api";

/**
 * fetch() for the engine's /code/* routes (files, git, terminal), ported from mentiko's
 * Next.js /api/fs/* and /api/git routes. Callers keep mentiko's URLs and response envelope:
 *
 *   codeFetch("/api/fs/tree?workspace=...")  ->  GET {engine}/code/fs/tree?workspace=...
 *   codeFetch("/api/git", { method: "POST" })  ->  POST {engine}/code/git
 *
 * Success bodies are `{ success: true, data }`; failures are a non-2xx status with
 * `{ success: false, error: { code, message } }`. Unlike fetch(), a non-2xx response does
 * NOT throw here: callers read `res.ok` and `unwrapCodeData(await res.json())` like mentiko.
 */
const api = new EngineApi();

export function codeUrl(url: string): string {
  if (url.startsWith("/api/fs/")) return `/code/fs/${url.slice("/api/fs/".length)}`;
  if (url === "/api/git" || url.startsWith("/api/git?")) return `/code/git${url.slice("/api/git".length)}`;
  return url.startsWith("/code/") ? url : `/code${url}`;
}

export async function codeFetch(url: string, init: RequestInit = {}): Promise<Response> {
  try {
    return await api.request(codeUrl(url), init);
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

/** The engine WebSocket URL for the terminal (auth happens in the first message, never in the URL). */
export function codeTerminalUrl(): Promise<string> {
  return api.websocketUrl("/code/terminal");
}

/** The bearer token for the first terminal message. */
export async function codeToken(): Promise<string> {
  await api.loadConfig();
  return api.getToken() ?? "";
}
