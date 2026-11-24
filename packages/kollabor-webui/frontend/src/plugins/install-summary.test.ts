import { describe, expect, it, vi } from "vitest";
// @ts-expect-error Vitest runs in Node; the app tsconfig intentionally omits Node ambient types.
import { readFileSync } from "node:fs";
import { ApiError } from "@/api";
import { describePluginAdds, describeUses, errorText, PLUGIN_TRUST_NOTICE, discardReview, normalizeReviewJob, reviewFormState, shouldPollReview, shouldRetryPolling, showFetching, startReview } from "./install";

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

describe("plugin review manifest boundary", () => {
  const canary = "secret-token-canary-1397";
  const job = () => ({
    job_id: "review-1", status: "ready", action: "install", plugin_id: "hello", commit: "a".repeat(40),
    files_sha256: "b".repeat(64), source: "https://example.invalid/plugin.git", ref: "main",
    changed_folders: ["plugins/hello"], error: null, warning: null,
    manifest: {
      name: "Hello", version: "1.2.3", description: "A plugin", uses: ["run:python3"],
      services: { worker: { secrets: ["API_TOKEN"] } }, contributes: { views: ["home"] }, hooks: false, mcp: false,
    },
  });
  const cases = [
    ["manifest name", (value: Record<string, any>) => { value.manifest.name = canary; }],
    ["manifest version", (value: Record<string, any>) => { value.manifest.version = { token: canary }; }],
    ["manifest description", (value: Record<string, any>) => { value.manifest.description = canary; }],
    ["manifest uses", (value: Record<string, any>) => { value.manifest.uses = [`net:${canary}`]; }],
    ["service name", (value: Record<string, any>) => { value.manifest.services = { [canary]: { secrets: [canary] } }; }],
    ["service secrets", (value: Record<string, any>) => { value.manifest.services.worker.secrets = [canary]; }],
    ["manifest contributions", (value: Record<string, any>) => { value.manifest.contributes = { [canary]: [{ id: canary }] }; }],
    ["source", (value: Record<string, any>) => { value.source = `https://example.invalid/?token=${canary}`; }],
    ["ref", (value: Record<string, any>) => { value.ref = canary; }],
    ["changed folders", (value: Record<string, any>) => { value.changed_folders = [canary]; }],
    ["job error", (value: Record<string, any>) => { value.error = `Failed: ${canary}`; }],
    ["job warning", (value: Record<string, any>) => { value.warning = `Warning: ${canary}`; }],
  ] as const;
  it.each(cases)("normalizes malformed %s before review details render", (_field, mutate) => {
    const input = job();
    mutate(input);
    const normalized = normalizeReviewJob(input);
    expect(normalized).not.toBeNull();
    expect(JSON.stringify(normalized)).not.toContain(canary);
  });
  it.each([
    ["complete install response", (_value: Record<string, any>) => {}, true],
    ["complete update response", (value: Record<string, any>) => { value.action = "update"; }, true],
    ["missing action", (value: Record<string, any>) => { delete value.action; }, false],
    ["unknown action", (value: Record<string, any>) => { value.action = "remove"; }, false],
    ["missing plugin id", (value: Record<string, any>) => { delete value.plugin_id; }, false],
    ["missing source", (value: Record<string, any>) => { delete value.source; }, false],
    ["mistyped source", (value: Record<string, any>) => { value.source = { url: "https://example.invalid" }; }, false],
    ["missing commit", (value: Record<string, any>) => { delete value.commit; }, false],
    ["mistyped file hash", (value: Record<string, any>) => { value.files_sha256 = "not-a-hash"; }, false],
    ["missing update folders", (value: Record<string, any>) => { value.action = "update"; delete value.changed_folders; }, false],
    ["mistyped update folders", (value: Record<string, any>) => { value.action = "update"; value.changed_folders = "content.txt"; }, false],
  ] as const)("sets review completeness for %s", (_label, mutate, complete) => {
    const input: Record<string, any> = job();
    mutate(input);
    expect(normalizeReviewJob(input)?.review_complete).toBe(complete);
  });
  it("shows safe reasons for rejected plugin requests", () => {
    expect(errorText(new ApiError("Invalid plugin Git source", 422, {}))).toBe("Invalid plugin Git source");
    expect(errorText(new ApiError("Cannot safely update this plugin", 409, {}))).toBe("Cannot safely update this plugin");
  });
  it("preserves combined allowlisted job warnings", () => {
    const warning = "A previously running service could not be restarted; check its status; Plugin staging files could not be removed";
    expect(normalizeReviewJob({ ...job(), warning })?.warning).toBe(warning);
  });
});

describe("declared plugin uses", () => {
  it("shows when a plugin's coding agents run with a shell without asking", () => {
    expect(describeUses(["run:gh,git,codex,claude,python3,pgrep"]).onItsOwn).toEqual([
      "Runs: gh,git,codex,claude,python3,pgrep",
      "Its coding agents use a shell without asking.",
    ]);
  });

  it("explains mediated agent runs and granted secrets", () => {
    expect(describeUses(["agents", "agents:unattended", "secret:GITHUB_TOKEN"])).toEqual({
      throughKollab: [
        "Can start agent runs through Kollab.",
        "Agent-run limits are not enforced; unreported token use is not capped.",
        "Can run agent tools without asking; those agents can do anything you can.",
        "Can request the secret GITHUB_TOKEN through Kollab.",
      ],
      onItsOwn: [],
      other: [],
    });
  });

  it("discloses that agent run limits are not enforced before consent", () => {
    expect(describeUses(["agents"]).throughKollab).toContain(
      "Agent-run limits are not enforced; unreported token use is not capped.",
    );
  });

  it("lists network, file and other declarations", () => {
    expect(describeUses(["net:github.com,api.github.com", "files:~/.kollab/cache", "custom:feature"])).toEqual({
      throughKollab: [],
      onItsOwn: ["Talks to: github.com,api.github.com.", "Reads or writes: ~/.kollab/cache."],
      other: ["custom:feature"],
    });
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
    expect(installSource).toMatch(/if\s*\(!job\s*\|\|\s*job\.status\s*!==\s*"ready"\s*\|\|\s*!job\.review_complete\s*\|\|\s*!job\.commit\s*\|\|\s*!job\.files_sha256\)\s*return;/);
    expect(installSource).toMatch(/const ready = job\?\.status === "ready" && job\.review_complete && Boolean\(job\.commit && job\.files_sha256\)/);
    expect(installSource).toContain("pluginReviewOmissionSummary(job)");
    expect(installSource).toMatch(/body:\s*JSON\.stringify\(\{\s*commit:\s*job\.commit,\s*files_sha256:\s*job\.files_sha256\s*\}\)/);
    expect(installSource).toMatch(/\{ready\s*\?\s*<Button[\s\S]*?void confirm\(\);[\s\S]*?:\s*null\}/);
  });
});

describe("plugin dialog focus", () => {
  it("returns focus to the Add Plugin, Update, View Log, or confirmation opener", () => {
    expect(installSource).toMatch(/<DialogContent[\s\S]*?onCloseAutoFocus=\{onRestoreFocus\}/);
    expect(settingsSource).toMatch(/onCloseAutoFocus=\{restore(?:Operation|Log|Confirmation)Focus\}/);
    expect(settingsSource).toContain("operationOpener.current.focus()");
    expect(settingsSource).toContain("logOpener.current.focus()");
    expect(settingsSource).toContain("confirmationOpener.current.focus()");
    expect(settingsSource).toContain("onRestoreFocus={restoreOperationFocus}");
  });

  it("moves focus to plugin progress when Review or Check Again disappears", () => {
    expect(installSource).toMatch(/<p ref=\{progressRef\} tabIndex=\{-1\} role="status"/);
    expect(installSource).toContain("progressRef.current?.focus()");
  });
});

describe("plugin scroll regions", () => {
  it("gives the review details and service log named keyboard scroll targets", () => {
    expect(installSource).toMatch(/<div role="region" tabIndex=\{0\} aria-label="Plugin review details" className="[^"]*overflow-y-auto/);
    expect(settingsSource).toMatch(/<pre role="region" tabIndex=\{0\} aria-label=\{`\$\{service\.name\} log`\} className="[^"]*overflow-auto/);
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

describe("cancelling a plugin review", () => {
  it("keeps a cancelling job pollable and retries cleanup after a failed DELETE", async () => {
    const closeSource = installSource.slice(installSource.indexOf("const close ="), installSource.indexOf("const confirm ="));
    const json = vi.fn()
      .mockRejectedValueOnce(new ApiError("Cleanup failed", 503, {}))
      .mockResolvedValueOnce({ status: "cancelled" });
    const api = { json } as never;
    const onClose = vi.fn();
    let job = { job_id: "review-1", status: "staging" as "staging" | "cancelling" };
    const cancel = async () => {
      if (job.status === "staging" || job.status === "cancelling") {
        await discardReview(api, job.job_id);
        onClose();
      } else {
        onClose();
      }
    };

    const firstCancel = cancel().catch((error: unknown) => error);
    job = { ...job, status: "cancelling" };
    expect(shouldPollReview(job)).toBe(true);
    expect(closeSource).toMatch(/job\.status === "cancelling"/);
    expect(await firstCancel).toMatchObject({ status: 503 });
    expect(onClose).not.toHaveBeenCalled();

    await cancel();
    expect(json).toHaveBeenCalledTimes(2);
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe("plugin review after a failure", () => {
  const stagingAfterPollError = { status: "staging" } as const;
  it("keeps the source form closed after a polling error", () => {
    expect(reviewFormState("install", stagingAfterPollError, true)).toEqual({ source: false, review: false });
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
  it("reports a refresh failure after recovering a completed install", () => {
    const confirm = installSource.slice(installSource.indexOf("const confirm = async"), installSource.indexOf("const ready ="));
    expect(confirm).toMatch(/current\.status === "complete"[\s\S]*?try\s*\{\s*await onApplied\(\);\s*\} catch \{\s*setError\("Plugin installed, but the plugin list could not be refreshed\."\);\s*return;/);
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
  it("does not let a terminal-job release conflict block retry", async () => {
    const api = { json: async () => { throw new ApiError("Plugin job cannot be cancelled", 409, null); } };
    await expect(discardReview(api as never, "failed-job")).resolves.toBeUndefined();
  });
  it("does not release an error or cancelled job when closing the review", () => {
    const close = installSource.slice(installSource.indexOf("const close ="), installSource.indexOf("const confirm ="));
    expect(close).toMatch(/job\.status === "staging" \|\| job\.status === "ready"/);
  });
  it("keeps a failed update review retryable through the shared review request path", async () => {
    expect(reviewFormState("update", { status: "error" }).review).toBe(true);
    const requests: { path: string; init?: { method?: string; body?: string } }[] = [];
    const api = { json: async (path: string, init?: { method?: string; body?: string }) => {
      requests.push({ path, init });
      return { job_id: "update-job", status: "staging" };
    } };
    await startReview(api as never, { action: "update", pluginId: "hello", url: "", gitRef: "" });
    expect(requests).toEqual([{ path: "/plugins/hello/update", init: { method: "POST", body: "{}" } }]);
  });
  it("closes a stale ready review when cancellation reports that its state changed", () => {
    const close = installSource.slice(installSource.indexOf("const close ="), installSource.indexOf("const confirm ="));
    expect(close).toMatch(/caught instanceof ApiError && caught\.status === 409/);
    expect(close).toContain("onClose(); return;");
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
