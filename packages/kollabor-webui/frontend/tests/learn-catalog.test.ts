import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { build } from "vite";
import { buildPhoneLearnMediaMap, learnPhoneMediaPlugin } from "../scripts/learn-phone-media-plugin.ts";
import { resolveLearnMediaSource } from "../src/components/learn/media.ts";
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

function lessonText(topicId: LearnTopicId): string {
  const topic = getLearnTopic(topicId);
  return [topic.summary, topic.note, ...topic.workflows.flatMap((workflow) => [
    workflow.label,
    workflow.summary,
    workflow.entry.prerequisite,
    workflow.entry.ui?.join(" "),
    workflow.entry.command,
    workflow.success,
    workflow.note,
    ...workflow.steps.flatMap((step) => [step.action, step.expect]),
    ...workflow.commands?.flatMap((command) => [command.syntax, command.example, command.description, command.expect]) ?? [],
    ...workflow.recovery.flatMap((item) => [item.symptom, item.action]),
  ])].filter(Boolean).join(" ");
}

test("Learn catalog imports all eleven stable JSON topics with complete workflows and media", () => {
  assert.deepEqual(LEARN_TOPICS.map((topic) => topic.id), expectedIds);
  for (const topic of LEARN_TOPICS) {
    assert.ok(topic.title.length > 0, `${topic.id} needs a title`);
    assert.ok(topic.summary.length > 20, `${topic.id} needs a plain-language summary`);
    assert.ok(topic.workflows.length > 0, `${topic.id} needs a workflow`);
    assert.equal("howTo" in topic, false, `${topic.id} must not keep the old howTo compatibility field`);
    assert.equal("example" in topic, false, `${topic.id} must not keep the old example compatibility field`);
    assert.ok((topic.related ?? []).every((related) => expectedIds.includes(related)), `${topic.id} has an unknown related topic`);
    const firstMedia = topic.workflows[0]?.media;
    const overview = firstMedia?.kind === "video" ? firstMedia.poster : firstMedia?.src;
    assert.equal(overview, `/learn/${topic.id}/overview.webp`, `${topic.id} needs its own canonical overview image or video poster`);
    for (const workflow of topic.workflows) {
      assert.ok(workflow.id && workflow.label, `${topic.id} workflow needs stable UI identifiers`);
      assert.ok(workflow.steps.length >= 1, `${topic.id}/${workflow.id} needs ordered actions`);
      assert.ok(workflow.steps.every((step) => step.action.trim().length > 0), `${topic.id}/${workflow.id} has an empty step`);
      assert.ok(workflow.success.trim().length > 0, `${topic.id}/${workflow.id} needs an expected result`);
      assert.ok(workflow.recovery.length > 0, `${topic.id}/${workflow.id} needs recovery guidance`);
      for (const command of workflow.commands ?? []) {
        assert.ok(!/[\r\n]/.test(command.example), `${topic.id}/${workflow.id} command examples stay one logical line`);
        assert.ok(command.description.trim().length > 0);
      }
    }
  }
  assert.equal(getLearnTopic("phone-access").title, "PhoneLink");
  assert.equal(getLearnTopic("networks").title, "Connect");
});

test("Learn recovery symptoms are bare labels because the renderer appends the colon", () => {
  for (const topic of LEARN_TOPICS) {
    for (const workflow of topic.workflows) {
      for (const item of workflow.recovery) {
        assert.ok(
          !item.symptom.trimEnd().endsWith("."),
          `${topic.id}/${workflow.id} symptom "${item.symptom}" must not end with a period`,
        );
      }
    }
  }
});

test("Learn instructional paths use arrows while literal command examples remain free-form", () => {
  function check(value: unknown, location: string): void {
    if (typeof value === "string") {
      assert.ok(!/\s>\s/.test(value), `${location} uses > as a UI path separator`);
    } else if (Array.isArray(value)) {
      value.forEach((item, index) => check(item, `${location}[${index}]`));
    } else if (value && typeof value === "object") {
      for (const [key, item] of Object.entries(value)) {
        if (key !== "syntax" && key !== "example") check(item, `${location}.${key}`);
      }
    }
  }
  for (const topic of LEARN_TOPICS) check(topic, topic.id);
});

test("PhoneLink resolves Learn images only from the signed app's inline asset map", () => {
  const root = mkdtempSync(path.join(tmpdir(), "kollab-phone-learn-media-"));
  const topicsDir = path.join(root, "topics");
  const publicDir = path.join(root, "public");
  const imagePath = path.join(publicDir, "learn", "agents", "overview.webp");
  mkdirSync(topicsDir, { recursive: true });
  mkdirSync(path.dirname(imagePath), { recursive: true });
  writeFileSync(path.join(topicsDir, "agents.json"), JSON.stringify({ workflows: [{ media: { kind: "image", src: "/learn/agents/overview.webp" } }] }));
  writeFileSync(imagePath, Buffer.from("signed-image-fixture"));

  try {
    const map = buildPhoneLearnMediaMap(topicsDir, publicDir);
    const image = map["/learn/agents/overview.webp"];
    assert.equal(image, `data:image/webp;base64,${Buffer.from("signed-image-fixture").toString("base64")}`);
    assert.equal(resolveLearnMediaSource("/learn/agents/overview.webp", "/learn", map), "/learn/agents/overview.webp");
    assert.equal(resolveLearnMediaSource("/learn/agents/overview.webp", "/relay/v1/phone/learn", map), image);
    assert.equal(resolveLearnMediaSource("/learn/agents/overview.webp", "/relay/v1/phone/learn", {}), null);
    writeFileSync(path.join(topicsDir, "agents.json"), JSON.stringify({ workflows: [{ media: { kind: "image", src: "https://example.invalid/secret.webp" } }] }));
    assert.throws(() => buildPhoneLearnMediaMap(topicsDir, publicDir), /must use a local \/learn/);
    writeFileSync(path.join(topicsDir, "agents.json"), JSON.stringify({ workflows: [{ media: { kind: "image", src: "/learn/agents/missing.webp" } }] }));
    assert.throws(() => buildPhoneLearnMediaMap(topicsDir, publicDir), /signed Learn media is missing/);
    const outsideAsset = path.join(root, "private.webp");
    writeFileSync(outsideAsset, Buffer.from("private"));
    symlinkSync(outsideAsset, path.join(publicDir, "learn", "agents", "linked.webp"));
    writeFileSync(path.join(topicsDir, "agents.json"), JSON.stringify({ workflows: [{ media: { kind: "image", src: "/learn/agents/linked.webp" } }] }));
    assert.throws(() => buildPhoneLearnMediaMap(topicsDir, publicDir), /regular file inside public/);

  } finally {
    rmSync(root, { recursive: true });
  }
});

test("Vite embeds PhoneLink Learn assets in its signed app module", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "kollab-vite-phone-learn-"));
  const topicsDir = path.join(root, "topics");
  const publicDir = path.join(root, "public");
  const imagePath = path.join(publicDir, "learn", "agents", "overview.webp");
  mkdirSync(topicsDir, { recursive: true });
  mkdirSync(path.dirname(imagePath), { recursive: true });
  writeFileSync(path.join(topicsDir, "agents.json"), JSON.stringify({ workflows: [{ media: { kind: "image", src: "/learn/agents/overview.webp" } }] }));
  writeFileSync(imagePath, Buffer.from("signed-image-fixture"));
  const entry = path.join(root, "entry.ts");
  writeFileSync(entry, 'import media from "virtual:kollab-learn-media"; export default media;');

  try {
    await build({
      configFile: false,
      root,
      plugins: [learnPhoneMediaPlugin({ enabled: true, topicsDir, publicDir })],
      build: {
        outDir: path.join(root, "output"),
        emptyOutDir: true,
        minify: false,
        lib: { entry, formats: ["es"], fileName: "phone.js" },
      },
    });
    const bundle = readFileSync(path.join(root, "output", "phone.js.mjs"), "utf8");
    assert.match(bundle, /data:image\/webp;base64,c2lnbmVkLWltYWdlLWZpeHR1cmU=/);
  } finally {
    rmSync(root, { recursive: true });
  }
});

test("all lessons preserve high-value truthful behavior from the former catalog", () => {
  const profiles = lessonText("profiles");
  assert.match(profiles, /endpoint/i);
  assert.match(profiles, /ACP command/i);
  assert.match(profiles, /KOLLAB_<PROFILE>_API_KEY/);

  const hub = lessonText("hub-messaging");
  for (const command of ["/hub agents", "/hub status", "/hub msg <agent> <message>", "/hub broadcast <message>"]) {
    assert.ok(hub.includes(command), `hub lesson is missing ${command}`);
  }
  assert.match(hub, /messages are visible to all hub members/i);
  assert.match(hub, /never send passwords, API keys, private keys, or one-time access codes/i);
  assert.doesNotMatch(hub, /<hub_msg|hub_msg to=/);

  const phone = lessonText("phone-access");
  for (const detail of [/pairing/i, /newest messages first/i, /load older history on demand/i, /retry action if a load fails/i, /expand a tool, so long chats open fast/i]) {
    assert.match(phone, detail);
  }
  assert.doesNotMatch(phone, /WireGuard|Advanced Settings/i);

  const voice = lessonText("voice");
  for (const detail of [
    /\/config → Hub → Dream Model/, /model ID/, /active provider/, /kollabor\.llm\.fast_model/,
    /active session model/, /endpoint and credentials/, /dreaming only/, /does not change voice replies/,
    /Settings → Voice → (?:Voice Preferences → )?Quick Model/, /\/voice config → Quick Model/, /any configured provider['’]s model/,
    /OpenRouter/, /Same as Chat/, /\/voice status/, /\/voice on/, /\/voice off/,
  ]) assert.match(voice, detail);
  assert.match(getLearnTopic("voice").note ?? "", /never turns on the microphone/i);
  assert.doesNotMatch(voice, /voice (?:replies|responses) (?:now )?use|not wired/i);

  const agents = lessonText("agents");
  for (const detail of [/Web UI/, /engine restarts/, /conversation/, /Delete/, /\/quit/, /Configure & Launch/, /workspace and profile/, /effective reporting tree/, /saved per team and workspace folder/]) {
    assert.ok(agents.match(detail), `agents lesson is missing ${detail}`);
  }
});

test("Learn URLs round-trip, preserve PhoneLink routes, and make invalid IDs actionable", () => {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", { configurable: true, value: { location: { pathname: "/relay/v1/phone/learn" } } as unknown as Window });
  try {
    assert.equal(learnHref("agents"), "/relay/v1/phone/learn?topic=agents");
  } finally {
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else Reflect.deleteProperty(globalThis, "window");
  }

  assert.equal(learnHref(), "/learn");
  assert.equal(learnHref("phone-access"), "/learn?topic=phone-access");
  assert.deepEqual(readLearnRoute({ pathname: "/learn", search: "" }), { kind: "topic", topicId: "networks" });
  assert.deepEqual(readLearnRoute({ pathname: "/learn/", search: "?topic=system-prompts" }), { kind: "topic", topicId: "system-prompts" });
  assert.deepEqual(readLearnRoute({ pathname: "/learn", search: "?topic=missing" }), { kind: "invalid", requestedTopic: "missing" });
  assert.equal(readLearnRoute({ pathname: "/", search: "?topic=agents" }), null);
  assert.deepEqual(readLearnRoute({ pathname: "/relay/v1/phone/learn", search: "?topic=phone-access" }), { kind: "topic", topicId: "phone-access" });
});
