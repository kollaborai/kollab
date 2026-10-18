import assert from "node:assert/strict";
import test from "node:test";
import { imageSource, initialsOf, inkFor, isGemAvatar, launchRef, tileColor } from "../src/components/agents/avatar-model.ts";

test("initials are the first letters of the first and last word", () => {
  assert.equal(initialsOf("Maya Lindqvist", "maya"), "ML");
  assert.equal(initialsOf("Lapis", "lapis"), "L");
  assert.equal(initialsOf("Ana Maria Souza", "ana"), "AS");
  // No display name: the hub name, where a number after a dash reads as a second word.
  assert.equal(initialsOf(undefined, "lapis-2"), "L2");
  assert.equal(initialsOf("", "omar"), "O");
  assert.equal(initialsOf("  ", ""), "?");
  // A letter outside the BMP stays whole.
  assert.equal(initialsOf("\u{1d4d0}da", "a"), "\u{1d4d0}");
});

test("a persona keeps its own color, an unnamed color falls to a stable pick", () => {
  assert.deepEqual(tileColor([92, 138, 214], "maya"), [92, 138, 214]);
  assert.deepEqual(tileColor(null, "maya"), tileColor(undefined, "maya"));
  assert.deepEqual(tileColor([1, 2], "maya"), tileColor(null, "maya"));
  assert.notDeepEqual(tileColor(null, "maya"), tileColor(null, "omar-and-others"));
});

test("ink is dark on a light tile and light on a dark one", () => {
  assert.equal(inkFor([240, 220, 80]), "#16181d");
  assert.equal(inkFor([15, 82, 186]), "#ffffff");
  assert.equal(inkFor([92, 138, 214]), "#ffffff");
  assert.equal(inkFor([230, 225, 240]), "#16181d");
});

test("a served image is fetched with the token, an https one is shown as it is", () => {
  const served = { kind: "image", url: "/rosters/field-crew/avatars/avatars/rae.svg" } as const;
  assert.deepEqual(imageSource(served), { fetch: "/rosters/field-crew/avatars/avatars/rae.svg" });
  // A project roster is found through the folder the chat runs in.
  assert.deepEqual(imageSource(served, "/work/site one"), {
    fetch: "/rosters/field-crew/avatars/avatars/rae.svg?workspace=%2Fwork%2Fsite%20one",
  });
  const remote = { kind: "image", url: "https://example.org/rae.png" } as const;
  assert.deepEqual(imageSource(remote, "/work"), { direct: "https://example.org/rae.png" });
  // A picture picked in the studio is stored inline and shown as it is.
  const picked = { kind: "image", url: "data:image/webp;base64,UklGRg==" } as const;
  assert.deepEqual(imageSource(picked, "/work"), { direct: "data:image/webp;base64,UklGRg==" });
});

test("only a roster the folder does not draw from needs its name in the launch identity", () => {
  assert.equal(launchRef({ name: "lapis", roster: "gems" }, "gems"), "lapis");
  assert.equal(launchRef({ name: "maya", roster: "marketing-crew" }, "gems"), "marketing-crew/maya");
  assert.equal(launchRef({ name: "koordinator", roster: "" }, "marketing-crew"), "koordinator");
  assert.equal(launchRef({ name: "maya" }, "gems"), "maya");
});

test("only a gem, or no avatar, is drawn as a gem; the rest are flat tiles with no look to dress", () => {
  assert.equal(isGemAvatar(undefined), true);
  assert.equal(isGemAvatar(null), true);
  assert.equal(isGemAvatar({ kind: "gem" }), true);
  assert.equal(isGemAvatar({ kind: "initials" }), false);
  assert.equal(isGemAvatar({ kind: "emoji", value: "\u{1f3af}" }), false);
  assert.equal(isGemAvatar({ kind: "image", url: "/rosters/field-crew/avatars/rae.svg" }), false);
});
