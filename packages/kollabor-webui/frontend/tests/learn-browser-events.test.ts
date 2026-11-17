import assert from "node:assert/strict";
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
