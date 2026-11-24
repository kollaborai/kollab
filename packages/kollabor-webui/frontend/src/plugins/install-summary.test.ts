import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { describePluginAdds, describeUses, PLUGIN_TRUST_NOTICE, reviewFormState, showFetching, startReview } from "./install";

const installSource = readFileSync(new URL("./install.tsx", import.meta.url), "utf8");

describe("plugin review additions", () => {
  it("lists only additions as lowercase fragments", () => {
    expect(describePluginAdds({ python: true, engine: true, web: true, agents: true,
      hooks: false, mcp: false, contributes: { views: ["hello"] }, services: {} })).toEqual([
      "1 view", "code that runs in every agent", "engine routes", "browser code", "agent definitions",
      "no hooks", "no MCP servers",
    ]);
  });
  it("includes hooks, MCP and background processes when declared", () => {
    expect(describePluginAdds({ hooks: true, mcp: true, services: { server: {} } })).toEqual([
      "hooks", "MCP servers", "1 background process",
    ]);
  });
  it("says when hooks and MCP servers are absent", () => {
    expect(describePluginAdds({})).toEqual(["no hooks", "no MCP servers"]);
  });
});

describe("declared plugin uses", () => {
  it("shows when a plugin's coding agents run with a shell without asking", () => {
    expect(describeUses(["run:gh,git,codex,claude,python3,pgrep"]).onItsOwn).toEqual([
      "Runs: gh,git,codex,claude,python3,pgrep",
      "Its coding agents use a shell without asking.",
    ]);
  });

  it("keeps the trust explanation explicit", () => {
    expect(PLUGIN_TRUST_NOTICE).toContain("they have your files and keys and can do more");
  });
});

describe("plugin review sheet data", () => {
  it("labels an omitted ref as the default branch without guessing its name", () => {
    expect(installSource).toContain('job.ref ? ` (${job.ref})` : " (default branch)"');
  });

  it("does not show limit values the review job does not return", () => {
    expect(installSource).not.toContain('label="Limits"');
    expect(installSource).not.toContain("When available, agent limits");
  });

  it("does not read file or grant changes the review job does not return", () => {
    expect(installSource).not.toMatch(/job\.changes/);
  });

  it("uses Add Plugin on the install confirmation action", () => {
    expect(installSource).toContain('action === "install" ? "Add Plugin" : "Update"');
  });

  it("shows the commit and file hash that the review job returned", () => {
    expect(installSource).toContain("Commit: {job.commit}");
    expect(installSource).toContain("Files SHA-256: {job.files_sha256}");
  });

  it("confirms only a ready review and sends its exact displayed commit and file hash", () => {
    expect(installSource).toMatch(/if\s*\(!job\s*\|\|\s*job\.status\s*!==\s*"ready"\s*\|\|\s*!job\.commit\s*\|\|\s*!job\.files_sha256\)\s*return;/);
    expect(installSource).toMatch(/body:\s*JSON\.stringify\(\{\s*commit:\s*job\.commit,\s*files_sha256:\s*job\.files_sha256\s*\}\)/);
    expect(installSource).toMatch(/\{ready\s*\?\s*<Button[\s\S]*?void confirm\(\);[\s\S]*?:\s*null\}/);
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

  it("rejects a ref supplied both in the URL and the Branch or Tag field", async () => {
    const json = async () => ({ job_id: "unused", status: "staging" });
    await expect(startReview({ json } as never, {
      action: "install", url: "https://example.com/plugin#main", gitRef: "release",
    })).rejects.toThrow("Put the branch or tag in one field, not both.");
  });

  it("rejects a staging response with no job id", async () => {
    const json = async () => ({ status: "staging" });
    await expect(startReview({ json } as never, {
      action: "install", url: "https://example.com/plugin", gitRef: "",
    })).rejects.toThrow("The plugin manager did not return a review job.");
  });
});
