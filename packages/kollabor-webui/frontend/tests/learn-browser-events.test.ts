import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";

import { makeFixtures } from "./learn-browser-acceptance.mjs";

test("staged session events are not replayed after the event stream reconnects", async () => {
  const fixtures = makeFixtures({ events: [{ type: "notice", message: "save failed" }] });
  const request = {
    url: () => "http://127.0.0.1:7433/sessions/session-1/events",
    method: () => "GET",
    postData: () => null,
  };
  const getStream = async () => {
    let response: { body?: string } | undefined;
    await fixtures.respond({ request: () => request, fulfill: async (value: { body?: string }) => { response = value; } } as never);
    return response?.body ?? "";
  };

  assert.match(await getStream(), /"message":"save failed"/);
  assert.doesNotMatch(await getStream(), /"message":"save failed"/);
});

test("a failed session click handles the pending history response rejection", () => {
  const helperUrl = new URL("./learn-browser-acceptance.mjs", import.meta.url).href;
  const script = `
    const { waitForSessionReady } = await import(${JSON.stringify(helperUrl)});
    const page = {
      waitForResponse(_predicate, { timeout }) {
        return new Promise((_resolve, reject) => {
          setTimeout(() => reject(new Error("history response timed out")), timeout);
        });
      },
    };
    try {
      await waitForSessionReady(page, {
        historyPath: "/sessions/remote/history",
        clickTarget: { click: async () => { throw new Error("click timed out"); } },
        timeout: 25,
      });
    } catch (error) {
      if (error.message !== "click timed out") throw error;
      await new Promise((resolve) => setTimeout(resolve, 50));
      console.log("failure dump ran");
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  `;
  const child = spawnSync(process.execPath, ["--input-type=module", "--eval", script], {
    encoding: "utf8",
    timeout: 2_000,
  });

  assert.equal(child.status, 0, child.stderr || child.stdout);
  assert.match(child.stdout, /failure dump ran/);
});
