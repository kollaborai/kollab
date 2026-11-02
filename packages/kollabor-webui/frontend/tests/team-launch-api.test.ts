import assert from "node:assert/strict";
import { test } from "vitest";
import { EngineApi } from "../src/api.ts";

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
