// #962: prove New Session selects a profile's last completed model without a live provider.
// Usage: node tests/issue-962-new-session-browser-acceptance.mjs <output folder>
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { launchAcceptanceBrowser, makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const screenshotName = "issue-962-new-session-last-completed.png";

async function main() {
  const outputDir = process.argv[2];
  if (!outputDir) throw new Error("Usage: node tests/issue-962-new-session-browser-acceptance.mjs <output folder>");
  await fs.mkdir(outputDir, { recursive: true });

  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await launchAcceptanceBrowser();
    const fixtures = makeFixtures();
    const profileResponse = {
      profiles: [{
        name: "default",
        provider: "openai_responses",
        model: "unsupported-model",
        last_completed_turn: {
          model: "working-model",
          effort: "high",
          completed_at: "2026-11-17T12:00:00Z",
        },
        unavailable_models: ["unsupported-model"],
      }],
      active: "default",
      default_loadout: null,
    };
    const fixture = {
      ...fixtures,
      async respond(route) {
        const request = route.request();
        const url = new URL(request.url());
        const endpoint = url.pathname.replace(/^\/api(?=\/)/, "");
        if (request.method() === "GET" && endpoint === "/profiles") {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(profileResponse) });
        }
        if (request.method() === "GET" && endpoint === "/profiles/choices") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              choices: [{ profile: "default", provider: "openai_responses", model: "working-model", effort: "high" }],
              discovery: {},
            }),
          });
        }
        return fixtures.respond(route);
      },
    };
    const view = await openPage(browser, baseUrl, fixture);
    await view.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await view.page.getByTestId("new-session-options").click();
    const model = view.page.locator("#new-session-model");
    await model.waitFor({ state: "visible", timeout: 5000 });
    assert.match(await model.innerText(), /working-model/);
    assert.doesNotMatch(await model.innerText(), /unsupported-model/);
    await view.page.screenshot({ path: path.join(outputDir, screenshotName), animations: "disabled" });

    const created = view.fixture.createRequest;
    await view.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const payload = await Promise.race([
      created,
      new Promise((_, reject) => setTimeout(() => reject(new Error("New Session did not reach the mocked engine.")), 15000)),
    ]);
    assert.equal(payload.profile, "default");
    const pickDeadline = Date.now() + 5000;
    while (!view.fixture.profileSwitches().length && Date.now() < pickDeadline) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    assert.deepEqual(view.fixture.profileSwitches(), [
      { name: "default", model: "working-model", effort: "high" },
    ]);
    assert.deepEqual(view.pageErrors, []);
    assert.deepEqual(view.externalRequests, []);
    await view.context.close();
    console.log(`Issue 962 browser proof passed; screenshot: ${screenshotName}`);
  } finally {
    await browser?.close();
    await server.close();
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    console.error(error.stack ?? error);
    process.exitCode = 1;
  });
}
