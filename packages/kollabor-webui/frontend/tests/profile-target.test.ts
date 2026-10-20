import assert from "node:assert/strict";
import test from "node:test";

import {
  profileLoadWarningLines,
  profileTargetLabel,
} from "../src/components/shell/profile-target.ts";

test("profile target shows the real endpoint without credentials or query data", () => {
  assert.equal(
    profileTargetLabel({
      provider: "anthropic",
      base_url: "https://operator:password@api.z.ai/api/anthropic?key=secret#ignored",
    }),
    "Endpoint: api.z.ai/api/anthropic",
  );
});

test("ACP profile target shows only the adapter executable", () => {
  assert.equal(
    profileTargetLabel({
      provider: "acp",
      command: ["/opt/kollab/claude-agent-acp", "--token=secret"],
    }),
    "ACP adapter: claude-agent-acp",
  );
});

test("profile target identifies default provider endpoints and missing ACP commands", () => {
  assert.equal(
    profileTargetLabel({ provider: "openai" }),
    "openai default endpoint",
  );
  assert.equal(
    profileTargetLabel({ provider: "acp", command: [] }),
    "ACP command missing",
  );
});

test("profile load diagnostics are visible as stable name/reason rows", () => {
  assert.deepEqual(
    profileLoadWarningLines({ "broken-acp": "ACP command is missing" }),
    ["broken-acp: ACP command is missing"],
  );
  assert.deepEqual(profileLoadWarningLines(undefined), []);
});
