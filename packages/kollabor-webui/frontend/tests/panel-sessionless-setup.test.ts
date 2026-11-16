// Sessionless setup panels (#1114, #1115): the setup guide's Provider and
// Network steps must open Settings before any session exists. The api and
// the PanelHost mount decision own the pieces this pins.

import assert from "node:assert/strict";
import test from "node:test";

import { EngineApi } from "../src/api.ts";
import {
  PANEL_NAME,
  panelHostMount,
  sessionlessPanelName,
  sessionlessTabs,
} from "../src/components/panels/panel-model.ts";

test("sessionless setup panels go to /setup/panels (#1114/#1115)", () => {
  const api = new EngineApi("http://engine");
  assert.equal(api.panelPath("", "setup"), "/setup/panels/setup");
  assert.equal(
    api.panelPath("", "setup", "/actions/finish"),
    "/setup/panels/setup/actions/finish",
  );
  assert.equal(api.panelPath("", "connect"), "/setup/panels/connect");
  assert.equal(
    api.panelPath("", "connect", "/actions/join"),
    "/setup/panels/connect/actions/join",
  );
  // with a session, both stay session-scoped daemon panels
  assert.equal(api.panelPath("s1", "setup"), "/sessions/s1/panels/setup");
  assert.equal(api.panelPath("s1", "connect"), "/sessions/s1/panels/connect");
});

test("getPanel and panelAction use the sessionless setup branch", async () => {
  const api = new EngineApi("http://engine");
  const paths: string[] = [];
  (api as unknown as { json: (p: string) => Promise<unknown> }).json = async (
    p: string,
  ) => (paths.push(p), { ok: true });
  await api.getPanel("", "setup");
  await api.panelAction("", "setup", "finish", { values: { provider: "anthropic" } });
  await api.getPanel("", "connect");
  assert.deepEqual(paths, [
    "/setup/panels/setup",
    "/setup/panels/setup/actions/finish",
    "/setup/panels/connect",
  ]);
});

test("sessionless panel name: halted folder keeps connect-halt, setup mount serves the tab's panel", () => {
  assert.equal(sessionlessPanelName("network", "/f"), "connect-halt");
  assert.equal(sessionlessPanelName("network", ""), PANEL_NAME.network);
  assert.equal(sessionlessPanelName("setup", ""), PANEL_NAME.setup);
  assert.equal(PANEL_NAME.setup, "setup");
  assert.equal(PANEL_NAME.network, "connect");
});

test("sessionless tabs: halt shows Network only, the setup mount shows Provider and Network", () => {
  assert.deepEqual(sessionlessTabs(true), ["network"]);
  assert.deepEqual(sessionlessTabs(false), ["setup", "network"]);
});

test("panelHostMount: sessioned, halt waiter, sessionless setup, and closed", () => {
  const active = { session_id: "s1", halted: undefined } as never;
  const halted = { session_id: "s2", halted: "by mac" } as never;

  const open = panelHostMount(active, true, "");
  assert.deepEqual(open, { mount: true, key: "s1", session: active });

  const haltWait = panelHostMount(null, true, "/f");
  assert.deepEqual(haltWait, {
    mount: true,
    key: "halt",
    session: { session_id: "", workspace: "/f" },
  });

  // #1114/#1115: no session, no halt folder, Settings open -> mount anyway.
  const setup = panelHostMount(null, true, "");
  assert.deepEqual(setup, {
    mount: true,
    key: "setup",
    session: { session_id: "", workspace: "" },
  });

  const closed = panelHostMount(null, false, "");
  assert.deepEqual(closed, { mount: false, key: "", session: null });
});
