import assert from "node:assert/strict";
import test from "node:test";
import { EngineApi } from "../src/api.ts";

test("WebUI-created sessions are explicitly marked for restart persistence", async () => {
  const originalFetch = globalThis.fetch;
  let sessionHeaders: Headers | null = null;

  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url === "/api/config") return new Response(null, { status: 404 });
    if (url === "http://127.0.0.1:7433/sessions") {
      sessionHeaders = new Headers(init?.headers);
      return new Response(JSON.stringify({ session_id: "web-session" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    throw new Error(`Unexpected fetch in test: ${url}`);
  };

  try {
    const api = new EngineApi("http://127.0.0.1:7433");
    await api.createSession({ agent: "default" });
    assert.equal(sessionHeaders?.get("X-Kollab-Session-Source"), "webui");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
