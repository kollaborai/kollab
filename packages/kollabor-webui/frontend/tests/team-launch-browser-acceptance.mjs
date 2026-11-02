import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";
import { preview } from "vite";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDir, "..");
const sourceTeam = {
  name: "test-team",
  description: "Team launcher browser acceptance fixture",
  roster: "test",
  lead: "alice",
  source: "project",
  path: "/learn-demo/.kollab/teams/test-team.json",
  members: [
    member("alice", "Alice", null, "Lead"),
    member("bob", "Bob", "alice", "Manager"),
    member("carol", "Carol", "bob", "Worker"),
  ],
};
const destinationTeam = {
  ...sourceTeam,
  path: "/other-demo/.kollab/teams/test-team.json",
  members: [
    member("alice", "Alice", null, "Lead"),
    member("carol", "Carol", "alice", "Coordinator"),
    member("dana", "Dana", "carol", "Worker"),
  ],
};
const profiles = {
  active: "default",
  providers: [],
  profiles: [{
    name: "default",
    provider: "openai_responses",
    model: "gpt-6-luna",
    base_url: "https://api.example.invalid/v1",
    reasoning_effort: "max",
  }],
};
const localCoder = {
  name: "local-coder",
  identity: "local-coder",
  roster: "test",
  display_name: "Local Coder",
  avatar: { kind: "gem" },
  color: [36, 117, 190],
  agent: "coder",
  agent_type: "coder",
  profile: "default",
  available: true,
  active: false,
  pool: true,
};
const mockRequests = [];
let launchPayload = null;

function member(name, displayName, reportsTo, role) {
  return {
    persona: {
      name,
      identity: name,
      roster: "test",
      display_name: displayName,
      avatar: { kind: "gem" },
      color: [36, 117, 190],
      caste: role.toLowerCase(),
      role,
      agent: "coder",
      agent_type: "coder",
      profile: "default",
      available: true,
      active: false,
      pool: true,
      prompt: "",
      skills: [],
      personality: "",
    },
    role,
    agent: "coder",
    profile: "default",
    prompt: `${role} seat prompt`,
    reports_to: reportsTo,
  };
}

async function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function respond(route) {
  const request = route.request();
  const url = new URL(request.url());
  const endpoint = url.pathname.replace(/^\/api(?=\/)/, "");
  const method = request.method();
  mockRequests.push({ method, endpoint, query: url.search });

  if (endpoint === "/teams" && method === "GET") {
    const team = url.searchParams.get("workspace") === "/other-demo" ? destinationTeam : sourceTeam;
    return json(route, { teams: [team], problems: [] });
  }
  if (endpoint === "/teams/test-team/launch" && method === "POST") {
    launchPayload = request.postDataJSON();
    return json(route, {
      team: "test-team",
      workspace: "/other-demo",
      started: ["alice", "carol", "dana"],
      skipped: [],
      failed: [],
      excluded: [],
      notes: [],
      sessions: [],
    });
  }
  if (endpoint === "/sessions" && method === "GET") {
    return json(route, {
      sessions: [],
      network: { device: "team-smoke", folder: "/learn-demo", live: false, remote: [], contacts: [], networks: [] },
      offline: [],
      hidden: [],
    });
  }
  if (endpoint === "/profiles") return json(route, profiles);
  if (endpoint === "/rosters") {
    return json(route, { workspace: "/learn-demo", default: "test", rosters: [], problems: [] });
  }
  if (endpoint === "/agents/bundles") return json(route, { bundles: [{ name: "coder", agent_type: "coder" }] });
  if (endpoint.startsWith("/agents")) {
    return json(route, { agents: [localCoder], live: [], active: [], available: ["local-coder"] });
  }
  if (endpoint === "/workspaces") {
    return json(route, {
      home: { path: "/learn-demo", name: "Learn demo", live: false },
      recent: [{ path: "/other-demo", name: "Other demo", live: false }],
    });
  }
  if (endpoint === "/workspaces/browse") {
    const folder = url.searchParams.get("path") || "/learn-demo";
    return json(route, { path: folder, name: folder === "/other-demo" ? "Other demo" : "Learn demo", parent: null, dirs: [] });
  }
  if (endpoint.endsWith("/history")) {
    return json(route, {
      history: [], start_index: 0, end_index: 0, has_more_older: false, older_cursor: null,
      has_more_newer: false, newer_cursor: null, source: "mirror", stale: false, last_turn_error: null,
    });
  }
  if (endpoint.endsWith("/permissions")) return json(route, { pending_prompts: [], approval_mode: "trust_all" });
  if (endpoint.endsWith("/events")) return route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
  return json(route, {});
}

async function main() {
  const builtIndex = path.join(frontendRoot, "dist", "index.html");
  if (!existsSync(builtIndex)) {
    throw new Error("Built frontend is missing; run `npm run build` before this acceptance test.");
  }

  const server = await preview({
    root: frontendRoot,
    configFile: path.join(frontendRoot, "vite.config.ts"),
    preview: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  let browser;
  let context;
  try {
    const address = server.httpServer.address();
    if (!address || typeof address === "string") throw new Error("Vite preview did not expose a local listener.");
    const baseUrl = `http://127.0.0.1:${address.port}`;
    browser = await chromium.launch({
      headless: true,
      ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
      args: process.platform === "linux" ? ["--no-sandbox"] : [],
    });
    context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await context.addInitScript(() => localStorage.setItem("kollab.setup-guide.v1", "dismissed"));
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.route("**/*", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const isEngine = url.port === "7433" || url.port === "8080";
      const isSameOriginFetch = url.origin === baseUrl && ["fetch", "xhr"].includes(request.resourceType());
      if (isEngine || isSameOriginFetch) return respond(route);
      if (url.origin === baseUrl) return route.continue();
      return route.abort();
    });

    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await page.getByTestId("new-session-options").click();
    await page.getByRole("button", { name: "Launch a Team", exact: true }).click();
    const teamCard = page.locator('section[aria-label="Test Team"]');
    try {
      await teamCard.waitFor({ state: "visible", timeout: 10000 });
    } catch (error) {
      console.error("Browser page text after team listing:", await page.locator("body").innerText());
      console.error("Mocked engine requests:", JSON.stringify(mockRequests, null, 2));
      throw error;
    }
    await teamCard.getByRole("button", { name: "Configure & Launch", exact: true }).click();
    await page.getByRole("heading", { name: "Launch Test Team", exact: true }).waitFor({ state: "visible" });

    await page.getByRole("checkbox", { name: "Include Bob", exact: true }).uncheck();
    const treePreview = page.locator('section[aria-labelledby="team-launch-preview-heading"]');
    await treePreview.getByText("Not selected: Bob", { exact: true }).waitFor({ state: "visible" });
    const carolRow = treePreview.locator("li").filter({ hasText: "Carol" });
    assert.equal(await carolRow.evaluate((node) => node.style.paddingLeft), "1.25rem");

    await page.locator("#team-launch-folder").click();
    await page.getByRole("button", { name: /Other demo/ }).click();
    await page.getByRole("checkbox", { name: "Include Dana", exact: true }).waitFor({ state: "visible" });
    assert.equal(await page.getByRole("checkbox", { name: "Include Bob", exact: true }).count(), 0);

    const mission = "Ship the team-launch browser smoke";
    await page.getByLabel("Mission", { exact: true }).fill(mission);
    const launchResponsePromise = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname.replace(/^\/api(?=\/)/, "") === "/teams/test-team/launch"
        && response.request().method() === "POST";
    }, { timeout: 15000 });
    await page.getByRole("button", { name: "Launch Team", exact: true }).click();
    const launchResponse = await launchResponsePromise;
    assert.equal(launchResponse.status(), 200);
    const payload = launchResponse.request().postDataJSON();
    assert.deepEqual(payload, {
      workspace: "/other-demo",
      profile: "default",
      members: ["alice", "carol", "dana"],
      lead: "alice",
      mission,
    });
    await page.getByRole("status").waitFor({ state: "visible" });
    assert.deepEqual(pageErrors, [], "browser reported runtime errors");
    console.log(`Team launch browser acceptance passed against built preview ${baseUrl}.`);
    console.log(`Verified exclusion/re-parenting, destination-folder lookup, and payload ${JSON.stringify(launchPayload)}.`);
  } finally {
    await context?.close();
    await browser?.close();
    await new Promise((resolve, reject) => server.httpServer.close((error) => error ? reject(error) : resolve()));
  }
}

main().catch((error) => {
  console.error(error?.stack ?? error);
  process.exitCode = 1;
});
