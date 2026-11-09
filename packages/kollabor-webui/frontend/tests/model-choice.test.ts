import assert from "node:assert/strict";
import test from "node:test";
import { applyModelChoice, choiceLabel, matchesChoice, PickError } from "../src/components/shell/model-choice.ts";

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
  results: Record<string, { ok: boolean; message?: string } | Error> = {},
  running?: { model: string; effort: string },
  record: { model: string; effort: string; profile?: string } = { model: "old-model", effort: "" },
) {
  const calls: unknown[][] = [];
  const api = {
    // The engine answers with what the agent now runs: the pick, unless something outranks it.
    setSessionProfile: async (...args: unknown[]) => (
      calls.push(["profile", ...args]),
      { session_id: "s1", model: running?.model ?? args[2], effort: running?.effort ?? args[3] }
    ),
    // A result for "pick:gpt-6-luna" answers only that model's pick; one for "pick", every pick.
    panelAction: async (...args: unknown[]) => {
      calls.push(["panel", ...args]);
      const value = Object.values(args[3] as Record<string, string>)[0];
      const result = results[`${String(args[2])}:${value}`] ?? results[String(args[2])] ?? { ok: true };
      if (result instanceof Error) throw result; // a 502 or a dropped connection
      return result;
    },
    // The session as the engine reads it back; a test says what it reports.
    getSession: async (id: string) => (calls.push(["get", id]), { session_id: id, ...record }),
  };
  return { api: api as never, calls };
}

test("the label never names a model an ACP harness has not reported", () => {
  // Petalite on #553 round 8: with no model reported, the toolbar showed the profile's saved model.
  const harness = { provider: "acp", model: "opus" };
  assert.deepEqual(choiceLabel({ profile: "claude-code", model: "", effort: "high" }, harness), ["claude-code", "high", ""]);
  assert.deepEqual(choiceLabel({ ...codexLunaMax }, harness), ["gpt-6-luna", "max", "codex-cli"]);
  // A native profile runs its saved model, so it names that one.
  const native = { provider: "openai_responses", model: "gpt-6.1-terra" };
  assert.deepEqual(choiceLabel({ profile: "openai-oauth", model: "", effort: "" }, native), ["gpt-6.1-terra", "", "openai-oauth"]);
});

test("a pick never lays its model over a profile switched elsewhere meanwhile", async () => {
  // Petalite on #553 round 8: the session was switched while the pick ran, and
  // the result showed the new profile with the old pick's model and level.
  const record = { profile: "openai-oauth", model: "gpt-6.1-terra", effort: "low" };
  const { api } = fakeApi({}, undefined, record);
  assert.deepEqual(await applyModelChoice(api, { session_id: "s1", profile: "codex-cli" }, codexLunaMax), { session_id: "s1", ...record });
  const refused = fakeApi({ pick: { ok: false, message: "max is not offered" } }, undefined, record);
  const running = { session_id: "s1", profile: "codex-cli", model: "gpt-6-sol", effort: "high" };
  const error = await applyModelChoice(refused.api, running, codexLunaMax).catch((e) => e);
  assert.ok(error instanceof PickError);
  assert.deepEqual(error.session, { session_id: "s1", ...record }); // a refused pick reports it as it is too
});

test("a switch whose answer is lost says what runs, and the panel shows it", async () => {
  // Petalite on #553 round 8: the request failed after the daemon switched, and
  // the panel kept the old profile until a later poll.
  const calls: unknown[][] = [];
  const now = { profile: "openai-oauth", model: "gpt-6-luna", effort: "high" };
  const api = {
    setSessionProfile: async (...args: unknown[]) => {
      calls.push(["profile", ...args]);
      throw new Error("502: daemon unreachable");
    },
    panelAction: async () => ({ ok: true }),
    getSession: async (id: string) => (calls.push(["get", id]), { session_id: id, ...now }),
  } as never;
  for (const choice of [{ ...oauthLuna, effort: "high" }, codexLunaMax]) {
    const error = await applyModelChoice(api, { session_id: "s1", profile: "claude-code" }, choice).catch((e) => e);
    assert.ok(error instanceof PickError);
    assert.equal(error.message, "502: daemon unreachable. It now runs openai-oauth · gpt-6-luna · high.");
    assert.deepEqual(error.session, { session_id: "s1", ...now });
  }
  assert.equal(calls.filter(([kind]) => kind === "get").length, 2);
});

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
  // Amazonite on #553 round 6: the switch applied, but the panel kept the old session.
  const error = await applyModelChoice(api, { session_id: "s1", profile: "openai-oauth" }, oauthLuna).catch((e) => e);
  assert.ok(error instanceof PickError);
  assert.deepEqual([error.session.model, error.session.effort], ["gpt-5.4", "low"]);
});

test("an ACP pick switches the harness, then sets its model and level in one action", async () => {
  // The engine reads the harness's options back before an action answers. Ametrine on #553 round 10: a
  // model action and a level action let a pick made in another window land between them.
  const { api, calls } = fakeApi({}, undefined, { profile: "codex-cli", model: "gpt-6-luna", effort: "max" });
  const shown = await applyModelChoice(api, { session_id: "s1", profile: "openai-oauth" }, codexLunaMax);
  assert.deepEqual([shown.model, shown.effort], ["gpt-6-luna", "max"]);
  assert.deepEqual(calls, [
    ["profile", "s1", "codex-cli"],
    ["panel", "s1", "model", "pick", { model: "gpt-6-luna", level: "max", profile: "codex-cli" }],
    ["get", "s1"],
  ]);
});

test("a pick shows what the engine reads back, so a newer one made elsewhere on that profile stays", async () => {
  // Rutile on #553 round 9: another window picked alpha-newer · max on the same profile before this
  // pick's read came back, and this panel showed its own older alpha-new · high instead.
  const newer = { profile: "codex-cli", model: "alpha-newer", effort: "max" };
  const { api } = fakeApi({}, undefined, newer);
  const pick = { ...codexLunaMax, model: "alpha-new", effort: "high" };
  const shown = await applyModelChoice(api, { session_id: "s1", profile: "codex-cli" }, pick);
  assert.deepEqual(shown, { session_id: "s1", ...newer });
});

test("a pick names its profile, so a switch made elsewhere never gets it", async () => {
  // Rutile on #553 round 9: the session moved to a native profile after the pick's model applied, and the
  // rollback then saved the ACP pick's old model to that native profile.
  const moved = "The session now runs openai-oauth, not codex-cli.";
  const now = { profile: "openai-oauth", model: "gpt-6-sol", effort: "low" };
  const { api, calls } = fakeApi({ pick: { ok: false, message: moved } }, undefined, now);
  const before = { session_id: "s1", profile: "codex-cli", model: "gpt-6.1-terra", effort: "high" };
  const error = await applyModelChoice(api, before, codexLunaMax).catch((e) => e);
  assert.ok(error instanceof PickError);
  assert.equal(error.message, `${moved} It now runs gpt-6-sol · low.`);
  assert.deepEqual(error.session, { session_id: "s1", ...now });
  const sent = calls.filter(([kind]) => kind === "panel").map((call) => call[4]);
  assert.deepEqual(sent, [{ model: "gpt-6-luna", level: "max", profile: "codex-cli" }]); // one action, nothing after
});

test("an ACP pick on the harness already running skips the switch; a refused pick says what runs", async () => {
  const { api, calls } = fakeApi({ pick: { ok: false, message: "max is not offered" } });
  await assert.rejects(
    applyModelChoice(api, { session_id: "s1", profile: "codex-cli" }, codexLunaMax),
    /max is not offered\. It now runs old-model\./,
  );
  assert.equal(calls[0][0], "panel");
});

test("a level the harness refuses leaves the model and level it had, as the engine reads them back", async () => {
  // Aragonite on #553 round 5 and Amazonite on round 6: the level was refused and the new model stayed.
  // The agent now puts both back inside the pick (test_acp_model_config_options); the picker shows them.
  const restored = { profile: "codex-cli", model: "gpt-6.1-terra", effort: "high" };
  const { api, calls } = fakeApi({ pick: { ok: false, message: "max is not offered" } }, undefined, restored);
  const error = await applyModelChoice(api, { session_id: "s1", ...restored }, codexLunaMax).catch((e) => e);
  assert.match(error.message, /max is not offered\. It now runs gpt-6\.1-terra · high\./);
  assert.deepEqual([error.session.model, error.session.effort], ["gpt-6.1-terra", "high"]);
  assert.deepEqual(calls.map(([kind]) => kind), ["panel", "get"]);
});

test("a pick whose answer is lost says what runs and sends nothing more", async () => {
  // Amazonite on #553 round 6: a 502 on the level left the new model running. The agent applies a pick or
  // puts it back as one change, so a lost answer needs only the session read back.
  const { api, calls } = fakeApi({ pick: new Error("502: daemon unreachable") }, undefined, {
    model: "gpt-6.1-terra",
    effort: "",
  });
  const before = { session_id: "s1", profile: "codex-cli", model: "gpt-6.1-terra", effort: "" };
  await assert.rejects(applyModelChoice(api, before, codexLunaMax), /502: daemon unreachable\. It now runs gpt-6\.1-terra\./);
  assert.deepEqual(calls.map(([kind]) => kind), ["panel", "get"]);
});

test("a harness that refuses a pick after the switch says the switch applied", async () => {
  // Fluorite on #553 round 7: it said nothing changed, and the panel kept the old profile.
  const { api } = fakeApi({ pick: { ok: false, message: "gpt-6-luna is not offered" } }, undefined, {
    profile: "codex-cli",
    model: "gpt-6.1-terra",
    effort: "high",
  });
  const before = { session_id: "s1", profile: "openai-oauth", model: "gpt-6-luna", effort: "" };
  const error = await applyModelChoice(api, before, codexLunaMax).catch((e) => e);
  assert.ok(error instanceof PickError);
  assert.match(error.message, /gpt-6-luna is not offered\. It now runs codex-cli · gpt-6\.1-terra · high\./);
  assert.equal(error.session.profile, "codex-cli");
});

test("a refused pick with no message names what it asked for and what the session now runs", async () => {
  // It moved to another harness: the error names what that harness reports, and carries it.
  const moved = fakeApi({ pick: { ok: false } }, undefined, { model: "gpt-6-luna", effort: "medium" });
  const error = await applyModelChoice(moved.api, { session_id: "s1", profile: "openai-oauth" }, codexLunaMax).catch(
    (e) => e,
  );
  assert.match(error.message, /codex-cli refused gpt-6-luna · max\. It now runs gpt-6-luna · medium\./);
  assert.deepEqual([error.session.model, error.session.effort], ["gpt-6-luna", "medium"]);
});
