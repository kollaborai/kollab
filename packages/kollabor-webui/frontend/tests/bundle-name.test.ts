// Run: node --test tests/bundle-name.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { bundleName } from "../src/components/panels/panel-model.ts";

test("a bundle id reads in Title Case", () => {
  assert.equal(bundleName("coder"), "Coder");
  assert.equal(bundleName("creative-writer"), "Creative Writer");
  assert.equal(bundleName("tech-dude"), "Tech Dude");
});

test("no bundle is the default one", () => {
  assert.equal(bundleName(), "Default");
  assert.equal(bundleName(""), "Default");
  assert.equal(bundleName(null), "Default");
});
