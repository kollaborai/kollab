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

test("session readiness bounds a stalled history body by one shared deadline", () => {
  const helperUrl = new URL("./learn-browser-acceptance.mjs", import.meta.url).href;
  const script = `
    const { waitForSessionReady } = await import(${JSON.stringify(helperUrl)});
    const timeout = 300;
    const startedAt = Date.now();
    let responseArrived = false;
    let bodyTimer;
    let bodyTimerCleared = false;
    const nativeSetTimeout = globalThis.setTimeout;
    const nativeClearTimeout = globalThis.clearTimeout;
    globalThis.setTimeout = (callback, delay, ...args) => {
      const timer = nativeSetTimeout(callback, delay, ...args);
      if (responseArrived) bodyTimer = timer;
      return timer;
    };
    globalThis.clearTimeout = (timer) => {
      if (timer === bodyTimer) bodyTimerCleared = true;
      return nativeClearTimeout(timer);
    };
    const response = {
      url: () => "http://127.0.0.1:7433/sessions/remote/history",
      request: () => ({ method: () => "GET" }),
      finished: () => new Promise(() => {}),
    };
    const page = {
      waitForResponse(_predicate, { timeout: responseTimeout }) {
        return new Promise((resolve) => {
          nativeSetTimeout(() => {
            responseArrived = true;
            resolve(response);
          }, responseTimeout * 0.75);
        });
      },
      waitForFunction() {
        throw new Error("unexpected session readiness wait");
      },
    };
    try {
      await waitForSessionReady(page, { historyPath: "/sessions/remote/history", timeout });
      throw new Error("helper resolved before the stalled history body");
    } catch (error) {
      if (error.message === "helper resolved before the stalled history body") throw error;
      if (!/history response body timed out/.test(error.message)) throw error;
      const elapsed = Date.now() - startedAt;
      if (elapsed > timeout + 100) throw new Error("helper exceeded its shared deadline: " + elapsed + "ms");
      if (!bodyTimerCleared) throw new Error("history body deadline timer was not cleared");
      console.log("stalled history body stayed within the shared deadline");
    } finally {
      globalThis.setTimeout = nativeSetTimeout;
      globalThis.clearTimeout = nativeClearTimeout;
    }
  `;
  const child = spawnSync(process.execPath, ["--input-type=module", "--eval", script], {
    encoding: "utf8",
    timeout: 1_500,
  });

  assert.equal(child.status, 0, child.stderr || child.stdout);
  assert.match(child.stdout, /stalled history body stayed within the shared deadline/);
});
