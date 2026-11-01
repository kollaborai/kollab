import assert from "node:assert/strict";
import test from "node:test";
import { applyModelChoice, matchesChoice } from "../src/components/shell/model-choice.ts";

const codexLunaMax = { profile: "codex-cli", provider: "acp", model: "gpt-6-luna", effort: "max" };
const oauthLuna = { profile: "openai-oauth", provider: "openai_responses", model: "gpt-6-luna", effort: "" };

test("every typed word must match: 'codex luna max' finds Codex CLI's luna at max only", () => {
  assert.equal(matchesChoice(codexLunaMax, "codex luna max"), true);
  assert.equal(matchesChoice(codexLunaMax, "  Codex   LUNA "), true);
  assert.equal(matchesChoice(oauthLuna, "codex luna max"), false);
  assert.equal(matchesChoice(oauthLuna, "luna default"), true); // no effort reads as "default"
  assert.equal(matchesChoice(oauthLuna, ""), true);
  // Aragonite on #553 round 5: an ACP row with no level keeps the harness's level, so it is no default.
  assert.equal(matchesChoice({ ...codexLunaMax, effort: "" }, "codex luna"), true);
  assert.equal(matchesChoice({ ...codexLunaMax, effort: "" }, "luna default"), false);
});

function fakeApi(
  results: Record<string, { ok: boolean; message?: string }> = {},
  running?: { model: string; effort: string },
) {
  const calls: unknown[][] = [];
  const api = {
    // The engine answers with what the agent now runs: the pick, unless something outranks it.
    setSessionProfile: async (...args: unknown[]) => (
      calls.push(["profile", ...args]),
      { session_id: "s1", model: running?.model ?? args[2], effort: running?.effort ?? args[3] }
    ),
    panelAction: async (...args: unknown[]) => (calls.push(["panel", ...args]), results[String(args[2])] ?? { ok: true }),
    // The session record lags behind the harness: it still names the old model.
    getSession: async (id: string) => (calls.push(["get", id]), { session_id: id, model: "old-model", effort: "" }),
  };
  return { api: api as never, calls };
}

test("a native pick is one switch that carries the model and effort", async () => {
  const { api, calls } = fakeApi();
  await applyModelChoice(api, { session_id: "s1", profile: "codex-cli" }, { ...oauthLuna, effort: "high" });
  assert.deepEqual(calls, [["profile", "s1", "openai-oauth", "gpt-6-luna", "high"]]);
});

test("a default pick sends an empty effort, which clears an earlier level", async () => {
  // Kunzite on #553 round 4: an empty effort was dropped, so the earlier level stayed.
  const { api, calls } = fakeApi();
  await applyModelChoice(api, { session_id: "s1", profile: "openai-oauth" }, oauthLuna);
  assert.deepEqual(calls, [["profile", "s1", "openai-oauth", "gpt-6-luna", ""]]);
  const env = fakeApi({}, { model: "gpt-6-luna", effort: "max" });
  await assert.rejects(
    applyModelChoice(env.api, { session_id: "s1", profile: "openai-oauth" }, oauthLuna),
    /kept gpt-6-luna · max/,
  );
});

test("a native pick an environment variable outranks is an error, not a pick shown as applied", async () => {
  const { api } = fakeApi({}, { model: "gpt-5.4", effort: "low" });
  await assert.rejects(
    applyModelChoice(api, { session_id: "s1", profile: "openai-oauth" }, { ...oauthLuna, effort: "high" }),
    /kept gpt-5\.4 · low\. KOLLAB_OPENAI_OAUTH_MODEL/,
  );
});

test("an ACP pick switches the harness, then sets its own model and effort options", async () => {
  const { api, calls } = fakeApi();
  const shown = await applyModelChoice(api, { session_id: "s1", profile: "openai-oauth" }, codexLunaMax);
  assert.deepEqual([shown.model, shown.effort], ["gpt-6-luna", "max"]); // what the harness took, not the lagging record
  assert.deepEqual(calls, [
    ["profile", "s1", "codex-cli"],
    ["panel", "s1", "model", "select", { model: "gpt-6-luna" }],
    ["panel", "s1", "model", "effort", { level: "max" }],
    ["get", "s1"],
  ]);
});

test("an ACP pick on the harness already running skips the switch; a refused option is an error", async () => {
  const { api, calls } = fakeApi({ effort: { ok: false, message: "max is not offered" } });
  await assert.rejects(
    applyModelChoice(api, { session_id: "s1", profile: "codex-cli" }, codexLunaMax),
    /max is not offered/,
  );
  assert.equal(calls[0][0], "panel");
});

test("a level the harness refuses puts its model back, so a pick never half-applies", async () => {
  // Aragonite on #553 round 5: the harness took the model, refused the level, and stayed on the new model.
  const { api, calls } = fakeApi({ effort: { ok: false, message: "max is not offered" } });
  await assert.rejects(
    applyModelChoice(api, { session_id: "s1", profile: "codex-cli", model: "gpt-6.1-terra" }, codexLunaMax),
    /max is not offered\. It stays on gpt-6\.1-terra\./,
  );
  assert.deepEqual(calls[calls.length - 1], ["panel", "s1", "model", "select", { model: "gpt-6.1-terra" }]);
  // A pick that moved to another harness has no model of its own to go back to: it says what runs.
  const moved = fakeApi({ effort: { ok: false } });
  await assert.rejects(
    applyModelChoice(moved.api, { session_id: "s1", profile: "openai-oauth", model: "gpt-6.1-terra" }, codexLunaMax),
    /codex-cli refused max\. It runs gpt-6-luna at the level it had\./,
  );
});
