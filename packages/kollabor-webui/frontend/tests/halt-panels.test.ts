import assert from "node:assert/strict";
import test from "node:test";
import { EngineApi, SESSIONLESS_PANELS } from "../src/api.ts";
import { confirmStep } from "../src/components/panels/confirm.ts";
import { haltedLine } from "../src/components/shell/halted-line.ts";

test("connect-halt and connect-halt-new go to /network/halt with the folder, the rest to the session", () => {
  const api = new EngineApi("http://engine");
  api.haltFolder = "/Users/me/dev/kollab";
  assert.deepEqual([...SESSIONLESS_PANELS].sort(), ["connect-halt", "connect-halt-new"]);
  assert.equal(api.panelPath("s1", "connect-halt"), "/network/halt/connect-halt?folder=%2FUsers%2Fme%2Fdev%2Fkollab");
  assert.equal(
    api.panelPath("s1", "connect-halt-new", "/actions/halt"),
    "/network/halt/connect-halt-new/actions/halt?folder=%2FUsers%2Fme%2Fdev%2Fkollab",
  );
  assert.equal(api.panelPath("s1", "connect"), "/sessions/s1/panels/connect");
  assert.equal(api.panelPath("s1", "connect-knocks", "/actions/x"), "/sessions/s1/panels/connect-knocks/actions/x");
});

test("getPanel and panelAction use the switch", async () => {
  const api = new EngineApi("http://engine");
  api.haltFolder = "/f";
  const paths: string[] = [];
  (api as unknown as { json: (p: string) => Promise<unknown> }).json = async (p: string) => (paths.push(p), { ok: true });
  await api.getPanel("s1", "connect-halt", { q: "x" });
  await api.panelAction("s1", "connect-halt", "propose_resume");
  await api.getPanel("s1", "connect", { q: "x" });
  assert.deepEqual(paths, [
    "/network/halt/connect-halt?folder=%2Ff",
    "/network/halt/connect-halt/actions/propose_resume?folder=%2Ff",
    "/sessions/s1/panels/connect?q=x",
  ]);
});

test("a confirm action arms first and fires on the second press (FormPanel, toolbar)", () => {
  assert.equal(confirmStep(null, "halt:", true), "arm");
  assert.equal(confirmStep("halt:", "halt:", true), "fire");
  assert.equal(confirmStep("other:", "halt:", true), "arm");
  assert.equal(confirmStep(null, "save:", undefined), "fire");
});

const halted = (rows: { actions?: string[] }[], toolbar: { id: string }[] = []) =>
  ({
    panel: "connect-halt",
    kind: "picker",
    summary: [{ label: "Network State", value: "Halted by mac at Oct 26 14:02 (their clock): runaway agent" }],
    rows,
    toolbar_actions: toolbar,
  }) as never;

test("the sidebar line shows only while halted, names the issuer and the next step", () => {
  assert.equal(haltedLine(null), null);
  assert.equal(
    haltedLine({ panel: "connect-halt", kind: "picker", summary: [{ label: "Network State", value: "Running" }] } as never),
    null,
  );
  assert.deepEqual(haltedLine(halted([], [{ id: "propose_resume" }])), {
    text: "Network halted by mac at Oct 26 14:02 (their clock)",
    link: "Propose Resume",
  });
  assert.equal(haltedLine(halted([{ actions: ["withdraw"] }]))?.link, "Waiting for a second machine");
  assert.equal(haltedLine(halted([{ actions: ["confirm_resume"] }]))?.link, "Confirm Resume");
});
