import assert from "node:assert/strict";
import test from "node:test";

import {
  profileLoadWarningLines,
  profileTargetLabel,
  shouldSubmitBaseUrlUpdate,
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
      acp_adapter: "claude-agent-acp",
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
    profileTargetLabel({ provider: "acp" }),
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


test("unchanged redacted endpoints are omitted from profile updates", () => {
  assert.equal(
    shouldSubmitBaseUrlUpdate("https://api.example/v1", "https://api.example/v1", true, false),
    false,
  );
  assert.equal(
    shouldSubmitBaseUrlUpdate("https://api.example/v1", "https://api.example/v2", true, false),
    true,
  );
  assert.equal(
    shouldSubmitBaseUrlUpdate("https://api.example/v1", "https://api.example/v1", true, true),
    true,
  );
  assert.equal(
    shouldSubmitBaseUrlUpdate("https://api.example/v1", "https://api.example/v1", false, false),
    true,
  );
});


test("profile target preserves a server-redacted path marker", () => {
  assert.equal(
    profileTargetLabel({
      provider: "anthropic",
      base_url: "https://api.example.test/v1/credential/[redacted]",
    }),
    "Endpoint: api.example.test/v1/credential/[redacted]",
  );
});
