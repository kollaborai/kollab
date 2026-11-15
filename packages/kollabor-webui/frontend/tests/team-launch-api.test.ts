import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import { ApiError, EngineApi } from "../src/api.ts";
import { PromptsApi, PromptsApiError } from "../src/prompts-api.ts";

test("team launch forwards selection fields and preserves the full-team default", async () => {
  const originalFetch = globalThis.fetch;
  const requests: { url: string; body: Record<string, unknown> }[] = [];
  globalThis.fetch = async (input, init) => {
    requests.push({
      url: String(input),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(
      JSON.stringify({
        team: "studio",
        workspace: "/work/a",
        started: [],
        skipped: [],
        failed: [],
        excluded: [],
        sessions: [],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };

  try {
    const api = new EngineApi("http://engine");
    await api.launchTeam("studio", "/work/a", "fast", {
      members: ["lead", "writer"],
      lead: "writer",
      mission: "Review the draft",
    });
    await api.launchTeam("studio", "/work/a", undefined, { exclude: ["analyst"] });
    await api.launchTeam("studio", "/work/a");

    const launches = requests.filter(({ url }) => url.endsWith("/teams/studio/launch"));
    assert.equal(launches.length, 3);
    assert.deepEqual(launches[0]?.body, {
      workspace: "/work/a",
      profile: "fast",
      members: ["lead", "writer"],
      lead: "writer",
      mission: "Review the draft",
    });
    assert.deepEqual(launches[1]?.body, {
      workspace: "/work/a",
      exclude: ["analyst"],
    });
    assert.deepEqual(launches[2]?.body, { workspace: "/work/a" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function errorResponse(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("engine request errors show detail while retaining the HTTP status", async () => {
  vi.stubGlobal("fetch", async (input: string | URL | Request) =>
    String(input) === "/api/config"
      ? new Response("{}", { status: 200 })
      : errorResponse(400, "Provider activation failed; previous profile was restored"),
  );

  const api = new EngineApi("http://engine");
  await assert.rejects(api.request("/profiles/activate"), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.message, "Provider activation failed; previous profile was restored");
    assert.equal(error.status, 400);
    return true;
  });
});

test("authentication refresh errors show detail while retaining the HTTP status", async () => {
  vi.stubGlobal("fetch", async () => errorResponse(503, "Refresh service unavailable"));

  const api = new EngineApi("http://engine");
  await assert.rejects(api.refreshToken(), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.message, "Refresh service unavailable");
    assert.equal(error.status, 503);
    return true;
  });
});

test("configuration errors show detail while retaining the HTTP status", async () => {
  vi.stubGlobal("fetch", async () => errorResponse(502, "Configuration proxy failed"));

  const api = new EngineApi("http://engine");
  await assert.rejects(api.loadConfig(), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.message, "Configuration proxy failed");
    assert.equal(error.status, 502);
    return true;
  });
});

test("prompt API errors show detail while retaining the HTTP status", async () => {
  const api = new PromptsApi({
    request: async () => errorResponse(404, "Prompt bundle not found"),
  });

  await assert.rejects(api.listPrompts(), (error: unknown) => {
    assert.ok(error instanceof PromptsApiError);
    assert.equal(error.message, "Prompt bundle not found");
    assert.equal(error.status, 404);
    return true;
  });
});

test("engine request errors retain a visible fallback when status text and detail are absent", async () => {
  let call = 0;
  vi.stubGlobal("fetch", async () =>
    ++call === 1
      ? new Response("{}", { status: 200 })
      : new Response("upstream failed", { status: 503, statusText: "" }),
  );

  const api = new EngineApi("http://engine");
  await assert.rejects(api.request("/profiles"), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.message, "HTTP 503");
    assert.equal(error.status, 503);
    return true;
  });
});

test("prompt API errors retain a visible fallback when status text and detail are absent", async () => {
  const api = new PromptsApi({
    request: async () => new Response("upstream failed", { status: 503, statusText: "" }),
  });

  await assert.rejects(api.listPrompts(), (error: unknown) => {
    assert.ok(error instanceof PromptsApiError);
    assert.equal(error.message, "HTTP 503");
    assert.equal(error.status, 503);
    return true;
  });
});
