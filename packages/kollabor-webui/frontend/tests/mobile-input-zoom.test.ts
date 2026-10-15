import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"

const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8")

test("mobile composer editables stay at least 16px to prevent iOS focus zoom", () => {
  const mobileRule = styles.match(/@media \(max-width: 767px\) \{([\s\S]*?)\n\}/)?.[1] ?? ""
  assert.match(mobileRule, /\.aui-composer-root \.aui-composer-input/)
  assert.match(mobileRule, /\.aui-edit-composer-root \.aui-edit-composer-input/)
  assert.match(mobileRule, /\[contenteditable="true"\]/)
  assert.match(mobileRule, /font-size: 16px !important/)
})
