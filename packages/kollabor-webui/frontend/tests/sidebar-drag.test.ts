import test from "node:test";
import assert from "node:assert/strict";
import { hiddenAgentTransform, outsideSidebar, sidebarDropAction } from "../src/components/shell/sidebar-drag.ts";

test("a released row shrinks into the footer button across sizes and positions", () => {
  assert.equal(hiddenAgentTransform(
    { left: 10, top: 100, width: 200, height: 80 },
    { left: 20, top: 800, width: 40, height: 32 },
  ), "translate(-70px, 676px) scale(0.02)");
  assert.equal(hiddenAgentTransform(
    { left: 480, top: 20, width: 280, height: 60 },
    { left: 700, top: 620, width: 32, height: 32 },
  ), "translate(96px, 586px) scale(0.02)");
});

test("hiding requires leaving the sidebar by more than the gutter", () => {
  const bounds = { left: 8, right: 220 };
  for (const x of [0, 8, 110, 220, 251, 252, NaN, Infinity, -Infinity, -10]) {
    assert.equal(outsideSidebar(x, bounds), false, `x=${x}`);
  }
  assert.equal(outsideSidebar(253, bounds), true);
  assert.equal(outsideSidebar(447, { left: 480, right: 800 }), true);
  assert.equal(outsideSidebar(448, { left: 480, right: 800 }), false);
  assert.equal(outsideSidebar(833, { left: 480, right: 800 }), true);
  assert.equal(outsideSidebar(500, { left: 220, right: 220 }), false);
});

const drop = {
  reason: "DROP" as const,
  mode: "FLUID" as const,
  source: { droppableId: "workspace-a", index: 0 },
  destination: { droppableId: "workspace-a", index: 1 },
};

test("outside pointer drops hide; pointer and keyboard drops inside can reorder", () => {
  assert.deepEqual(sidebarDropAction(drop, true), { type: "hide" });
  assert.deepEqual(sidebarDropAction({ ...drop, destination: null }, true), { type: "hide" });
  assert.deepEqual(sidebarDropAction(drop, false), { type: "reorder", index: 1 });
  assert.deepEqual(sidebarDropAction({ ...drop, mode: "SNAP" }, true), { type: "reorder", index: 1 });
});

test("Escape, touch cancellation and unchanged or invalid drop zones never hide", () => {
  for (const outside of [false, true]) {
    assert.equal(sidebarDropAction({ ...drop, reason: "CANCEL" }, outside), null);
  }
  assert.equal(sidebarDropAction({ ...drop, destination: null }, false), null);
  assert.equal(sidebarDropAction({ ...drop, destination: drop.source }, false), null);
  assert.equal(sidebarDropAction({ ...drop, destination: { droppableId: "workspace-b", index: 1 } }, false), null);
  assert.equal(sidebarDropAction({ ...drop, mode: "SNAP", destination: null }, true), null);
});

test("the last row in a workspace can be hidden without reordering", () => {
  assert.deepEqual(sidebarDropAction({ ...drop, destination: drop.source }, true), { type: "hide" });
  assert.equal(sidebarDropAction({ ...drop, destination: drop.source }, false), null);
});
