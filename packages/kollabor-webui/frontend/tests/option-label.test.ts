import assert from "node:assert/strict";
import test from "node:test";
import { optionLabel } from "../src/components/panels/panel-model.ts";

const field = { option_labels: { gems: "Gems", "marketing-crew": "Marketing Crew" } };

test("a dropdown option reads as the label the daemon gave it", () => {
  assert.equal(optionLabel(field, "gems"), "Gems");
  assert.equal(optionLabel(field, "marketing-crew"), "Marketing Crew");
});

test("an option with no label, or a field with no labels at all, reads as itself", () => {
  assert.equal(optionLabel(field, "my-team"), "my-team");
  assert.equal(optionLabel({ option_labels: null }, "none"), "none");
  assert.equal(optionLabel({}, "none"), "none");
  assert.equal(optionLabel(field, ""), "");
});

test("the empty option reads as the daemon's label for it (Same as Chat)", () => {
  assert.equal(optionLabel({ option_labels: { "": "Same as Chat" } }, ""), "Same as Chat");
});
