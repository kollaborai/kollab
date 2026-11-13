import assert from "node:assert/strict";
import test from "node:test";
import { extractControlCandidates } from "../scripts/inventory-webui-controls.mjs";

test("discovers native and shared interactive components without claiming coverage", () => {
  const source = `<section>\n  <Button aria-label="Send" onClick={send} />\n  <DropdownMenuItem onSelect={choose}>Open</DropdownMenuItem>\n  <button title={dynamic}>Go</button>\n  <div>Not a control</div>\n</section>`;
  const found = extractControlCandidates(source, "src/example.tsx");
  assert.deepEqual(found.map(({ tag, line }) => [tag, line]), [["Button", 2], ["DropdownMenuItem", 3], ["button", 4]]);
  assert.equal(found[0].attributes["aria-label"], '"Send"');
  assert.equal(found[2].attributes.title, "{dynamic}");
  assert.equal(found[1].textHint, "Open");
  assert.equal(found[0].attributes.onClick, "{send}");
  assert.ok(found.every((item) => item.coverage === "unverified"));
});
