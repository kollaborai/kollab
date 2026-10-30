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
  "context-compaction",
  "koordinator",
  "hub-messaging",
  "voice",
  "system-prompts",
  "phone-access",
];

test("Learn catalog keeps all eleven public topic IDs stable and useful", () => {
  assert.deepEqual(LEARN_TOPICS.map((topic) => topic.id), expectedIds);
  for (const topic of LEARN_TOPICS) {
    assert.ok(topic.title.length > 0, `${topic.id} needs a title`);
    assert.ok(topic.summary.length > 20, `${topic.id} needs a plain-language summary`);
    assert.ok(topic.howTo.length >= 3, `${topic.id} needs a short how-to`);
    assert.ok(topic.example.length > 15, `${topic.id} needs an example`);
  }
  assert.equal(getLearnTopic("phone-access").title, "PhoneLink");

  const profile = getLearnTopic("profiles");
  const profileGuidance = [profile.summary, ...profile.howTo, profile.example, profile.note].join(" ");
  assert.match(profileGuidance, /endpoint/i);
  assert.match(profileGuidance, /ACP command/i);
  assert.match(profileGuidance, /KOLLAB_<PROFILE>_API_KEY/);
});


test("PhoneLink lesson no longer advertises manual WireGuard setup", () => {
  const phone = getLearnTopic("phone-access");
  const guidance = [phone.summary, ...phone.howTo, phone.example, phone.note].join(" ");

  assert.match(guidance, /pairing/i);
  assert.match(guidance, /newest messages first/i);
  assert.match(guidance, /load older history on demand/i);
  assert.match(guidance, /retry action if a load fails/i);
  assert.match(guidance, /expand a tool, so long chats open fast/i);
  assert.doesNotMatch(guidance, /WireGuard|Advanced Settings/i);
});

test("voice lesson documents the Dream Model control without overstating voice impact", () => {
  const voice = getLearnTopic("voice");
  const guidance = [voice.summary, ...voice.howTo, voice.example, voice.note].join(" ");

  // Where the control lives and what it accepts.
  assert.match(guidance, /\/config > Hub > Dream Model/);
  assert.match(guidance, /model ID/);
  assert.match(guidance, /active provider/);

  // Blank-value fallback chain, in order.
  assert.match(guidance, /kollabor\.llm\.fast_model/);
  assert.match(guidance, /active session model/);

  // Credential reuse, not new auth.
  assert.match(guidance, /endpoint and credentials/);

  // Scope truth: dreaming only; voice replies are the Quick Model's job.
  assert.match(guidance, /dreaming only/);
  assert.match(guidance, /does not change voice replies/);

  // The Quick Model control: where it lives and what it accepts.
  assert.match(guidance, /Settings → Voice → Quick Model/);
  assert.match(guidance, /\/voice config → Quick Model/);
  assert.match(guidance, /any configured provider's model/);
  assert.match(guidance, /OpenRouter/);
  assert.match(guidance, /Same as Chat/);

  // Guard against invented integration claims.
  assert.doesNotMatch(guidance, /voice (?:replies|responses) (?:now )?use/);
  assert.doesNotMatch(guidance, /not wired/);
});

test("voice lesson keeps its microphone lifecycle steps intact", () => {
  const voice = getLearnTopic("voice");
  const guidance = [voice.summary, ...voice.howTo].join(" ");
  assert.match(guidance, /\/voice status/);
  assert.match(guidance, /\/voice on/);
  assert.match(guidance, /\/voice off/);
  assert.match(voice.note, /never turns on the microphone/);
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
  assert.deepEqual(readLearnRoute({ pathname: "/relay/v1/phone/learn", search: "?topic=phone-access" }), {
    kind: "topic", topicId: "phone-access",
  });
});


test("agents lesson explains Web UI persistence and explicit stop actions", () => {
  const agents = getLearnTopic("agents");
  const guidance = [agents.summary, ...agents.howTo, agents.example, agents.note].join(" ");
  assert.match(guidance, /Web UI/);
  assert.match(guidance, /engine restarts/);
  assert.match(guidance, /conversation/);
  assert.match(guidance, /Delete/);
  assert.match(guidance, /\/quit/);
});
