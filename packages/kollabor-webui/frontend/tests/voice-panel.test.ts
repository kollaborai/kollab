import assert from "node:assert/strict";
import test from "node:test";
import { panelCommandRequest, PANEL_NAME, SETTINGS_TABS } from "../src/components/panels/panel-model.ts";

const commands = [{ name: "voice", description: "Device voice", panel: "voice" }];

test("voice configuration opens its tab while listening commands reach the daemon", () => {
  assert.deepEqual(panelCommandRequest("/voice config", commands), { tab: "voice" });
  assert.deepEqual(panelCommandRequest(" /VOICE CONFIG ", commands), { tab: "voice" });
  for (const text of ["/voice", "/voice on", "/voice off", "/voice status", "/voice retry", "/voice context 20", "/voice config extra", "/voicemode", "/voicemodels"]) {
    assert.equal(panelCommandRequest(text, commands), null, text);
  }
  assert.equal(PANEL_NAME.voice, "voice");
  assert.equal(SETTINGS_TABS.find(tab => tab.id === "voice")?.label, "Voice");
});

test("other settings and network actions retain their routing", () => {
  assert.deepEqual(panelCommandRequest("/config", []), { tab: "configuration" });
  assert.deepEqual(panelCommandRequest("/connect code", []), { tab: "network", intent: "new_code" });
  assert.equal(panelCommandRequest("/model effort high", []), null);
});

test("the System Prompts settings tab opens directly", () => {
  const promptsTab = SETTINGS_TABS.find((tab) => tab.id === "prompts");
  assert.deepEqual(panelCommandRequest("/prompts", []), { tab: "prompts" });
  assert.equal(PANEL_NAME.prompts, "prompts");
  assert.equal(promptsTab?.label, "Prompts");
  assert.ok(promptsTab?.scope.includes("~/.kollab/prompt-overrides"));
});
