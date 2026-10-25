import test from "node:test";
import assert from "node:assert/strict";
import { relayDetachedTouches } from "../src/components/shell/touch-relay.ts";

class Finger extends EventTarget {
  isConnected = true;
  onHandle: boolean;
  constructor(onHandle = true) { super(); this.onHandle = onHandle; }
  closest(selector: string) { return this.onHandle && selector.includes("rfd-drag-handle") ? this : null; }
}

function touch(type: string, x: number, y: number) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "touches", { value: type === "touchend" ? [] : [{ clientX: x, clientY: y }] });
  Object.defineProperty(event, "changedTouches", { value: [{ clientX: x, clientY: y }] });
  return event;
}

function setup(onHandle = true) {
  const win = new EventTarget();
  const seen: Array<{ type: string; x?: number; y?: number }> = [];
  for (const type of ["touchmove", "touchend", "touchcancel"]) {
    win.addEventListener(type, (event) => {
      const touches = (event as unknown as { touches: Array<{ clientX: number; clientY: number }> }).touches;
      seen.push({ type, x: touches[0]?.clientX, y: touches[0]?.clientY });
      event.preventDefault(); // what the drag library does with a drag in progress
    });
  }
  const stop = relayDetachedTouches(win);
  const finger = new Finger(onHandle);
  const start = new Event("touchstart", { bubbles: true });
  Object.defineProperty(start, "target", { value: finger });
  win.dispatchEvent(start);
  return { win, finger, seen, stop };
}

test("a touch whose row was unmounted still reaches the window, and its scroll is cancelled", () => {
  const { finger, seen } = setup();
  finger.isConnected = false;
  const move = touch("touchmove", 12, 34);
  finger.dispatchEvent(move);
  assert.deepEqual(seen, [{ type: "touchmove", x: 12, y: 34 }]);
  assert.equal(move.defaultPrevented, true);
  finger.dispatchEvent(touch("touchend", 12, 34));
  assert.equal(seen.at(-1)?.type, "touchend");
});

test("a touch whose row is still mounted is left to the window", () => {
  const { finger, seen } = setup();
  finger.dispatchEvent(touch("touchmove", 1, 2));
  assert.deepEqual(seen, []);
});

test("touches that do not start on a drag handle are not followed", () => {
  const { finger, seen } = setup(false);
  finger.isConnected = false;
  finger.dispatchEvent(touch("touchmove", 1, 2));
  assert.deepEqual(seen, []);
});

test("the relay stops after the touch ends and when it is removed", () => {
  const { finger, seen, stop } = setup();
  finger.isConnected = false;
  finger.dispatchEvent(touch("touchend", 1, 2));
  finger.dispatchEvent(touch("touchmove", 3, 4));
  assert.equal(seen.length, 1);
  stop();
});
