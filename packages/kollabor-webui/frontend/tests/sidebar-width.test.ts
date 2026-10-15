import assert from "node:assert/strict"
import test from "node:test"
import {
  clampSidebarWidth,
  draggedSidebarWidth,
  keyboardSidebarWidth,
  sidebarWidthBounds,
  sidebarWidthStyle,
  storedSidebarWidth,
} from "../src/components/ui/sidebar-width.ts"

test("desktop widths leave 320 pixels for chat and never exceed 480", () => {
  assert.deepEqual(sidebarWidthBounds(820), { min: 220, max: 480 })
  assert.deepEqual(sidebarWidthBounds(768), { min: 220, max: 448 })
  assert.equal(clampSidebarWidth(480, 768), 448)
  assert.equal(clampSidebarWidth(1000, 1920), 480)
  assert.equal(clampSidebarWidth(20, 1920), 220)
})

test("left and right dividers move with the pointer, including captured moves beyond the viewport", () => {
  assert.equal(draggedSidebarWidth(256, 40, "left", 1200), 296)
  assert.equal(draggedSidebarWidth(256, 40, "right", 1200), 220)
  assert.equal(draggedSidebarWidth(256, -40, "right", 1200), 296)
  assert.equal(draggedSidebarWidth(256, 5000, "left", 820), 480)
  assert.equal(draggedSidebarWidth(256, -5000, "left", 820), 220)
})

test("keyboard movement follows the divider side, with larger Shift steps and bounded Home/End", () => {
  assert.equal(keyboardSidebarWidth("ArrowRight", 300, "left", 1200), 308)
  assert.equal(keyboardSidebarWidth("ArrowLeft", 300, "right", 1200), 308)
  assert.equal(keyboardSidebarWidth("ArrowRight", 300, "right", 1200, true), 268)
  assert.equal(keyboardSidebarWidth("Home", 300, "left", 1200), 220)
  assert.equal(keyboardSidebarWidth("End", 300, "right", 768), 448)
  assert.equal(keyboardSidebarWidth("ArrowLeft", 220, "left", 1200), 220)
  assert.equal(keyboardSidebarWidth("Enter", 300, "left", 1200), null)
})

test("only finite in-range device preferences are restored", () => {
  for (const bad of [null, "", " ", "null", "NaN", "Infinity", "219", "481", "100000"])
    assert.equal(storedSidebarWidth(bad), null)
  assert.equal(storedSidebarWidth("320"), 320)
  assert.equal(storedSidebarWidth("220"), 220)
  assert.equal(storedSidebarWidth("480"), 480)
  // A narrow viewport clamps display without corrupting the wider saved preference.
  assert.equal(clampSidebarWidth(storedSidebarWidth("480")!, 768), 448)
  assert.equal(clampSidebarWidth(storedSidebarWidth("480")!, 1200), 480)
})

test("provider CSS preserves caller rem/calc defaults within shared bounds", () => {
  assert.match(sidebarWidthStyle("20rem"), /, 20rem, /)
  assert.match(sidebarWidthStyle("calc(20rem + 4px)"), /, calc\(20rem \+ 4px\), /)
  assert.match(sidebarWidthStyle(320), /, 320px, /)
  assert.match(sidebarWidthStyle(320), /calc\(100vw - 320px\)/)
})
