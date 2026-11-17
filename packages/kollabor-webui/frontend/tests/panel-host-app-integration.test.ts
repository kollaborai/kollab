import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const app = readFileSync(resolve(here, "../src/App.tsx"), "utf8");

test("App mounts PanelHost from the sessionless setup decision", () => {
  // The pure panel-model tests pin the decision. This pins the App wiring that
  // makes that decision visible when no session or halt folder exists.
  assert.ok(app.includes("panelHostMount(activeSession, settingsOpen, api.haltFolder)"), "App must use the tested mount decision");
  assert.ok(app.includes("{host.mount ? ("), "App must mount PanelHost when the decision allows it");
  assert.ok(app.includes("key={host.key}"), "the host key must follow the mount decision");
});
