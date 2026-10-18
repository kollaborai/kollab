// Run: node --test tests/session-subtitle.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { sessionFolder, sessionSubtitle } from "../src/utils/session-subtitle.ts";

test("an engine-started and a terminal-started agent read the same", () => {
  const started = { model: "claude-opus-5-5", workspace: "/home/user/dev/kollab" };
  const adopted = { ...started, external: true } as typeof started;
  assert.equal(sessionSubtitle(started), "claude-opus-5-5 · kollab");
  assert.equal(sessionSubtitle(adopted), sessionSubtitle(started));
});

test("same-name agents in two folders tell apart by folder", () => {
  const a = sessionSubtitle({ model: "claude-opus-5-5", workspace: "/home/user" });
  const b = sessionSubtitle({ model: "claude-opus-5-5", workspace: "/home/user/dev/kollab" });
  assert.notEqual(a, b);
});

test("an unknown model leaves the folder alone, in the same format", () => {
  assert.equal(sessionSubtitle({ workspace: "/w/app" }), "app");
  assert.equal(sessionSubtitle({ model: "", workspace: "/w/app" }), "app");
});

test("off hub leads, and an agent with nothing known still reads", () => {
  assert.equal(sessionSubtitle({ model: "m", workspace: "/w/app" }, true), "Off Hub · m · app");
  assert.equal(sessionSubtitle({}), "Running");
  assert.equal(sessionFolder({ workspace: "/" }), "");
  assert.equal(sessionFolder({ workspace: null }), "");
});
