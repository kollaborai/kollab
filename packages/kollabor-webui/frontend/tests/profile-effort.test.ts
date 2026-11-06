// Run: node --test tests/profile-effort.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { sendsAnthropic } from "../src/components/shell/profile-effort.ts";

test("a named provider decides on its own", () => {
  assert.equal(sendsAnthropic({ provider: "anthropic" }, ""), true);
  assert.equal(sendsAnthropic({ provider: "openai", api_key: "sk-ant-x" }, ""), false);
});

test("auto follows a typed key first, the way the server resolves it", () => {
  assert.equal(sendsAnthropic({ provider: "auto", api_key: "sk-ant-x" }, ""), true);
  assert.equal(sendsAnthropic({ provider: "auto", api_key: "sk-x" }, ""), false);
  assert.equal(
    sendsAnthropic(
      { provider: "auto", api_key: "sk-x", base_url: "https://api.anthropic.com/v1" },
      "",
    ),
    false,
  );
});

test("auto with a typed key that has no known prefix follows the endpoint", () => {
  assert.equal(
    sendsAnthropic(
      { provider: "auto", api_key: "custom-123", base_url: "https://api.anthropic.com/v1" },
      "",
    ),
    true,
  );
  assert.equal(
    sendsAnthropic(
      { provider: "auto", api_key: "custom-123", base_url: "https://openrouter.ai/api/v1" },
      "",
    ),
    false,
  );
});

test("a saved auto profile uses the provider the server resolved, since its key is never sent back", () => {
  assert.equal(sendsAnthropic({ provider: "auto", api_key: "", base_url: "" }, "anthropic"), true);
  assert.equal(sendsAnthropic({ provider: "auto", api_key: "", base_url: "" }, "openai"), false);
});

test("a new auto profile with no key follows the endpoint typed in the form", () => {
  assert.equal(
    sendsAnthropic({ provider: "auto", api_key: "", base_url: "https://api.anthropic.com" }, ""),
    true,
  );
  assert.equal(sendsAnthropic({ provider: "auto", api_key: "", base_url: "" }, ""), false);
});
