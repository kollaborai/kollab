import assert from "node:assert/strict";
import test from "node:test";
import { pagePath, readMainPageRoute } from "../src/utils/page-route.ts";

test("release updates have a separate route from Learn", () => {
  assert.deepEqual(readMainPageRoute({ pathname: "/whats-new", search: "" }), { kind: "whats-new" });
  assert.deepEqual(readMainPageRoute({ pathname: "/learn", search: "?topic=agents" }), {
    kind: "learn", learn: { kind: "topic", topicId: "agents" },
  });
  assert.deepEqual(readMainPageRoute({ pathname: "/", search: "" }), { kind: "chat" });
});

test("page entry and close preserve the existing PhoneLink app prefix", () => {
  assert.deepEqual(readMainPageRoute({ pathname: "/relay/v1/phone/whats-new", search: "" }), { kind: "whats-new" });
  assert.deepEqual(readMainPageRoute({ pathname: "/relay/v1/phone/learn", search: "?topic=agents" }), {
    kind: "learn", learn: { kind: "topic", topicId: "agents" },
  });
  assert.equal(pagePath("/", "/relay/v1/phone/learn"), "/relay/v1/phone/");
  assert.equal(pagePath("/whats-new", "/relay/v1/phone/"), "/relay/v1/phone/whats-new");
  assert.equal(pagePath("/", "/learn"), "/");
  assert.equal(pagePath("/whats-new", "/relay/v1/phone-other"), "/whats-new");
});

test("unknown Learn topics retain recovery instead of opening release updates", () => {
  assert.deepEqual(readMainPageRoute({ pathname: "/learn", search: "?topic=missing" }), {
    kind: "learn", learn: { kind: "invalid", requestedTopic: "missing" },
  });
  assert.deepEqual(readMainPageRoute({ pathname: "/whats-new", search: "?topic=agents" }), { kind: "whats-new" });
});
