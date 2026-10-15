import assert from "node:assert/strict";
import test from "node:test";
import { cliHarness } from "../src/utils/cli-harness.ts";

test("native profiles never acquire a CLI badge from a vendor-like name", () => {
  assert.equal(cliHarness({ provider: "anthropic", profile: "claude-code" }), null);
  assert.equal(cliHarness({ provider: "google", profile: "gemini-cli" }), null);
  assert.equal(cliHarness({ profile: "anthropic" }), null);
});

test("renamed ACP profiles keep their declared harness brand", () => {
  assert.deepEqual(cliHarness({ provider: "acp", profile: "reviewer", harness: "Codex over ACP" }), {
    brand: "codex", label: "Codex CLI",
  });
  assert.deepEqual(cliHarness({ harness: "Claude Code over ACP" }), {
    brand: "claude", label: "Claude Code CLI",
  });
  assert.deepEqual(cliHarness({ provider: "acp", profile: "antigravity" }), {
    brand: "gemini", label: "Antigravity CLI",
  });
});

test("custom ACP commands use a generic mark without guessing from their profile name", () => {
  assert.deepEqual(cliHarness({ provider: "acp", profile: "not-codex" }), {
    brand: null, label: "Agent CLI",
  });
});
