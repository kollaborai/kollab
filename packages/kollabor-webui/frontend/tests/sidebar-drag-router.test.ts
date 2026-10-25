import assert from "node:assert/strict";
import test from "node:test";
import type { DropResult } from "@hello-pangea/dnd";
import { sidebarDropAction } from "../src/components/shell/sidebar-drag.ts";
import { createSidebarDragRouter, type SidebarDragHandlers } from "../src/components/shell/sidebar-drag-router.ts";

const deviceScope = '["devices","network-a"]';
const agentScope = '["remote","network-a","device-a","workspace-a"]';
const deviceKey = '["device","network-a","device-a"]';
const agentKey = '["pos","remote","network-a","device-a","workspace-a","lapis@device-a"]';
const provided = { announce: () => {} };
const drop = (scope: string, key: string, changes: Partial<DropResult> = {}): DropResult => ({
  draggableId: key,
  type: scope,
  reason: "DROP",
  mode: "FLUID",
  source: { droppableId: scope, index: 0 },
  destination: { droppableId: scope, index: 1 },
  combine: null,
  ...changes,
});

function list(key: string, outside = false) {
  const calls = { lifts: 0, ends: 0, saves: 0, hides: 0, holds: 0 };
  const handlers: SidebarDragHandlers = {
    owns: (candidate) => candidate === key,
    beforeCapture: () => { calls.lifts += 1; calls.holds += 1; },
    end: (result) => {
      try {
        calls.ends += 1;
        const action = sidebarDropAction(result, outside);
        if (action?.type === "reorder") calls.saves += 1;
        if (action?.type === "hide") calls.hides += 1;
      } finally { calls.holds -= 1; }
    },
  };
  return { calls, handlers };
}

test("device and nested agent gestures dispatch only to their originating controller", () => {
  const router = createSidebarDragRouter();
  const device = list(deviceKey);
  const agent = list(agentKey, true);
  router.register(deviceScope, device.handlers);
  router.register(agentScope, agent.handlers);

  assert.equal(router.beforeCapture({ draggableId: deviceKey, mode: "SNAP" }), true);
  router.end(drop(deviceScope, deviceKey, { mode: "SNAP" }), provided);
  assert.deepEqual(device.calls, { lifts: 1, ends: 1, saves: 1, hides: 0, holds: 0 });
  assert.deepEqual(agent.calls, { lifts: 0, ends: 0, saves: 0, hides: 0, holds: 0 });

  router.beforeCapture({ draggableId: agentKey, mode: "FLUID" });
  router.end(drop(agentScope, agentKey, { destination: null }), provided);
  assert.deepEqual(agent.calls, { lifts: 1, ends: 1, saves: 0, hides: 1, holds: 0 });
  assert.equal(device.calls.hides, 0);
  assert.equal(device.calls.saves, 1);
});

test("cancel, unchanged and outside device drops release the hold without saving or hiding", () => {
  for (const changes of [
    { reason: "CANCEL" as const },
    { destination: null },
    { destination: { droppableId: deviceScope, index: 0 } },
    { destination: { droppableId: '["devices","another-network"]', index: 1 } },
    { source: { droppableId: agentScope, index: 0 } },
    { draggableId: agentKey },
    { type: agentScope },
  ]) {
    const router = createSidebarDragRouter();
    const device = list(deviceKey);
    const agent = list(agentKey, true);
    router.register(deviceScope, device.handlers);
    router.register(agentScope, agent.handlers);
    router.beforeCapture({ draggableId: deviceKey, mode: "FLUID" });
    router.end(drop(deviceScope, deviceKey, changes), provided);
    assert.deepEqual(device.calls, { lifts: 1, ends: 1, saves: 0, hides: 0, holds: 0 });
    assert.deepEqual(agent.calls, { lifts: 0, ends: 0, saves: 0, hides: 0, holds: 0 });
  }
});

test("a cross-scope agent result cancels even when its drag-out cue is active", () => {
  const router = createSidebarDragRouter();
  const agent = list(agentKey, true);
  router.register(agentScope, agent.handlers);
  router.beforeCapture({ draggableId: agentKey, mode: "FLUID" });
  router.end(drop(agentScope, agentKey, { destination: { droppableId: deviceScope, index: 0 } }), provided);
  assert.deepEqual(agent.calls, { lifts: 1, ends: 1, saves: 0, hides: 0, holds: 0 });
});

test("unknown or repeated completions cannot fall through to another list", () => {
  const router = createSidebarDragRouter();
  const device = list(deviceKey);
  router.register(deviceScope, device.handlers);
  assert.equal(router.beforeCapture({ draggableId: "unknown", mode: "FLUID" }), false);
  router.end(drop(deviceScope, deviceKey), provided);
  assert.equal(device.calls.ends, 0);
  router.beforeCapture({ draggableId: deviceKey, mode: "FLUID" });
  router.end(drop(deviceScope, deviceKey), provided);
  router.end(drop(deviceScope, deviceKey), provided);
  assert.equal(device.calls.ends, 1);
  assert.equal(device.calls.holds, 0);
});

test("a device clone's child registration survives the original child's cleanup", () => {
  const router = createSidebarDragRouter();
  const original = list(agentKey);
  const clone = list(agentKey);
  const cleanupOriginal = router.register(agentScope, original.handlers);
  router.register(agentScope, clone.handlers);
  cleanupOriginal();
  assert.equal(router.beforeCapture({ draggableId: agentKey, mode: "SNAP" }), true);
  router.end(drop(agentScope, agentKey, { mode: "SNAP" }), provided);
  assert.equal(original.calls.lifts, 0);
  assert.deepEqual(clone.calls, { lifts: 1, ends: 1, saves: 1, hides: 0, holds: 0 });
});
