import assert from "node:assert/strict";
import test from "node:test";
import {
  getLearnTopic,
  LEARN_TOPICS,
  learnHref,
  readLearnRoute,
  type LearnTopicId,
} from "../src/learn/catalog.ts";

const expectedIds: LearnTopicId[] = [
  "networks",
  "devices",
  "workspaces",
  "agents",
  "profiles",
  "koordinator",
  "hub-messaging",
  "voice",
  "system-prompts",
  "phone-access",
];

test("Learn catalog keeps all ten public topic IDs stable and useful", () => {
  assert.deepEqual(LEARN_TOPICS.map((topic) => topic.id), expectedIds);
  for (const topic of LEARN_TOPICS) {
    assert.ok(topic.title.length > 0, `${topic.id} needs a title`);
    assert.ok(topic.summary.length > 20, `${topic.id} needs a plain-language summary`);
    assert.ok(topic.howTo.length >= 3, `${topic.id} needs a short how-to`);
    assert.ok(topic.example.length > 15, `${topic.id} needs an example`);
  }
  assert.match(getLearnTopic("phone-access").title, /WireGuard/i);
});

test("Learn URLs round-trip and invalid topic IDs give an actionable route state", () => {
  assert.equal(learnHref(), "/learn");
  assert.equal(learnHref("phone-access"), "/learn?topic=phone-access");
  assert.deepEqual(readLearnRoute({ pathname: "/learn", search: "" }), {
    kind: "topic",
    topicId: "networks",
  });
  assert.deepEqual(
    readLearnRoute({ pathname: "/learn/", search: "?topic=system-prompts" }),
    { kind: "topic", topicId: "system-prompts" },
  );
  assert.deepEqual(readLearnRoute({ pathname: "/learn", search: "?topic=missing" }), {
    kind: "invalid",
    requestedTopic: "missing",
  });
  assert.equal(readLearnRoute({ pathname: "/", search: "?topic=agents" }), null);
});
