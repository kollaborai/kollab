import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { prefersReducedMotion } from "../src/components/learn/media.ts";

const globalScope = globalThis as { window?: unknown };
const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

function setWindow(value: unknown) {
  Object.defineProperty(globalThis, "window", { value, configurable: true, writable: true });
}

afterEach(() => {
  if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
  else delete globalScope.window;
});

test("prefersReducedMotion is false when there is no window", () => {
  delete globalScope.window;
  assert.equal(prefersReducedMotion(), false);
});

test("prefersReducedMotion is false when matchMedia is missing", () => {
  setWindow({});
  assert.equal(prefersReducedMotion(), false);
});

test("prefersReducedMotion reads the reduce query", () => {
  const queries: string[] = [];
  setWindow({
    matchMedia: (query: string) => {
      queries.push(query);
      return { matches: true };
    },
  });
  assert.equal(prefersReducedMotion(), true);
  assert.deepEqual(queries, ["(prefers-reduced-motion: reduce)"]);
});

test("prefersReducedMotion is false when the query does not match", () => {
  setWindow({ matchMedia: () => ({ matches: false }) });
  assert.equal(prefersReducedMotion(), false);
});
