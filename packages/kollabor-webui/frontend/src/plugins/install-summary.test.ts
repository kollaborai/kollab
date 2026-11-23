import { describe, expect, it } from "vitest";
import { describePluginAdds, reviewFormState, showFetching, startReview } from "./install";

describe("plugin review additions", () => {
  it("lists only additions as lowercase fragments", () => {
    expect(describePluginAdds({ python: true, engine: true, web: true, agents: true,
      hooks: false, mcp: false, contributes: { views: ["hello"] }, services: {} })).toEqual([
      "1 view", "code that runs in every agent", "engine routes", "browser code", "agent definitions",
    ]);
  });
  it("includes hooks, MCP and background processes when declared", () => {
    expect(describePluginAdds({ hooks: true, mcp: true, services: { server: {} } })).toEqual([
      "hooks", "MCP servers", "1 background process",
    ]);
  });
});

describe("plugin review form state", () => {
  it("keeps the source form and Review open after a failed review", () => {
    expect(reviewFormState("install", { status: "error" })).toEqual({ source: true, review: true });
  });
  it("opens the source form before any review starts", () => {
    expect(reviewFormState("install", null)).toEqual({ source: true, review: true });
  });
  it("hides the source form while a review is staging or ready", () => {
    expect(reviewFormState("install", { status: "staging" })).toEqual({ source: false, review: false });
    expect(reviewFormState("install", { status: "ready" })).toEqual({ source: false, review: false });
  });
  it("never shows the source form for an update", () => {
    expect(reviewFormState("update", null)).toEqual({ source: false, review: false });
    expect(reviewFormState("update", { status: "error" })).toEqual({ source: false, review: false });
  });
});

describe("plugin review after a failure", () => {
  const failedPoll = { status: "staging" } as const;
  it("reopens the source form and Review when the review stalled on an error", () => {
    expect(reviewFormState("install", failedPoll, true)).toEqual({ source: true, review: true });
  });
  it("keeps the form closed while a review is staging without an error", () => {
    expect(reviewFormState("install", failedPoll, false)).toEqual({ source: false, review: false });
  });
  it("shows the fetching line only while a review is staging without an error", () => {
    expect(showFetching(failedPoll, false)).toBe(true);
    expect(showFetching(failedPoll, true)).toBe(false);
    expect(showFetching({ status: "error" }, false)).toBe(false);
    expect(showFetching(null, false)).toBe(false);
  });
  it("sends the edited URL when Review runs again after a failure", async () => {
    const sent: { path: string; body: string }[] = [];
    let calls = 0;
    const api = { json: async (path: string, init?: { body?: string }) => {
      sent.push({ path, body: String(init?.body) });
      if (++calls === 1) throw new Error("could not reach the plugin manager");
      return { job_id: "job2", status: "staging", action: "install" };
    } };
    await expect(startReview(api as never, { action: "install", url: "https://example.com/wrong", gitRef: "" })).rejects.toThrow("could not reach");
    const job = await startReview(api as never, { action: "install", url: "https://example.com/right", gitRef: "v2" });
    expect(job.job_id).toBe("job2");
    expect(sent.map(item => item.path)).toEqual(["/plugins/install", "/plugins/install"]);
    expect(JSON.parse(sent[1].body)).toEqual({ source: "https://example.com/right#v2" });
  });
});
