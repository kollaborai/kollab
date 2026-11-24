import { describe, expect, it } from "vitest";
// @ts-expect-error Vitest runs in Node; the app tsconfig intentionally omits Node ambient types.
import { readFileSync } from "node:fs";
import { describePluginAdds, describeUses, PLUGIN_TRUST_NOTICE, reviewFormState, shouldRetryPolling, showFetching, startReview } from "./install";

const installSource = readFileSync(new URL("./install.tsx", import.meta.url), "utf8");
const settingsSource = readFileSync(new URL("./settings.tsx", import.meta.url), "utf8");

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
  it("uses text without adding decorative icons to the plugin surfaces", () => {
    expect(installSource).not.toMatch(/from ["']lucide-react["']/);
    expect(settingsSource).not.toMatch(/from ["']lucide-react["']/);
  });

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
  it("uses the form state helper for the source and Review controls", () => {
    expect(installSource).toContain("{form.source ? <div");
    expect(installSource).toContain("{form.review ? <Button");
  });
  it("never shows the source form for an update", () => {
    expect(reviewFormState("update", null)).toEqual({ source: false, review: false });
    expect(reviewFormState("update", { status: "error" })).toEqual({ source: false, review: true });
    expect(reviewFormState("update", null, true)).toEqual({ source: false, review: true });
  });
});

describe("plugin review after a failure", () => {
  const stagingAfterPollError = { status: "staging" } as const;
  it("keeps the source form closed after a polling error", () => {
    expect(reviewFormState("install", stagingAfterPollError)).toEqual({ source: false, review: false });
  });
  it("keeps the form closed while a review is staging without an error", () => {
    expect(reviewFormState("install", stagingAfterPollError)).toEqual({ source: false, review: false });
  });
  it("shows the fetching line only while a review is staging without an error", () => {
    expect(showFetching(stagingAfterPollError, false)).toBe(true);
    expect(showFetching(stagingAfterPollError, true)).toBe(false);
    expect(showFetching({ status: "error" }, false)).toBe(false);
    expect(showFetching(null, false)).toBe(false);
  });
  it("offers retry only for a staging job whose poll failed", () => {
    expect(shouldRetryPolling(stagingAfterPollError, true)).toBe(true);
    expect(shouldRetryPolling(stagingAfterPollError, false)).toBe(false);
    expect(shouldRetryPolling({ status: "error" }, true)).toBe(false);
  });
  it("keeps polling and offers a retry after a lost confirmation response", () => {
    const polling = installSource.slice(installSource.indexOf("  useEffect(() => {", installSource.indexOf("const start")), installSource.indexOf("  const close ="));
    expect(polling).toContain('job.status !== "staging" && job.status !== "installing"');
    expect(shouldRetryPolling({ status: "installing" }, true)).toBe(true);
  });
  it("treats a completed job fetched after a lost confirmation response as success", () => {
    const confirm = installSource.slice(installSource.indexOf("const confirm = async"), installSource.indexOf("const ready ="));
    expect(confirm).toContain('current.status === "complete"');
    expect(confirm).toContain("await onApplied()");
  });
  it("keeps the completed review open when the engine returns a warning", () => {
    const confirm = installSource.slice(installSource.indexOf("const confirm = async"), installSource.indexOf("const ready ="));
    expect(confirm).toMatch(/if\s*\(!result\.warning\)\s*onClose\(\)/);
    expect(installSource).toMatch(/\{job\?\.status === "complete" \? <p role="status"/);
    expect(installSource).toMatch(/\{job\?\.warning \? <p role="alert"/);
  });
  it("offers another poll for a staging job after a polling error", () => {
    expect(installSource).toContain("Check Again");
    expect(installSource).toMatch(/\{shouldRetryPolling\(job, error !== null\)\s*\? <Button[\s\S]*?setPollVersion\(\(version\) => version \+ 1\);[\s\S]*>Check Again<\/Button>/);
  });
  it("reloads the job after the confirm request fails", () => {
    const confirm = installSource.slice(installSource.indexOf("const confirm = async"), installSource.indexOf("const ready ="));
    expect(confirm).toMatch(/catch[\s\S]*fetchReview\(api,\s*job\.job_id\)/);
  });
  it("discards a prior job before closing or starting a replacement review", () => {
    const close = installSource.slice(installSource.indexOf("const close ="), installSource.indexOf("const confirm ="));
    const start = installSource.slice(installSource.indexOf("const start ="), installSource.indexOf("useEffect(() =>"));
    expect(close).toContain("discardReview(api, job.job_id)");
    expect(start).toContain("discardReview(api, job.job_id)");
  });
  it("explains when a review job was cancelled", () => {
    expect(installSource).toContain("Plugin review was cancelled.");
  });
  it("builds a new review request from the supplied URL and ref after a failed request", async () => {
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
