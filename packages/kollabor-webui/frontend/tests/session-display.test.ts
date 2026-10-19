// Run: node --test tests/session-display.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

// session-display.ts imports "../components/panels/panel-model" the way Vite resolves it; node needs the extension.
registerHooks({
  resolve: (specifier, context, next) => {
    try {
      return next(specifier, context);
    } catch (error) {
      if (!specifier.startsWith(".")) throw error;
      return next(`${specifier}.ts`, context);
    }
  },
});
const { formatSessionName } = await import("../src/utils/session-display.ts");

test("a generated id shows its two words", () => {
  assert.equal(formatSessionName(null, "2610180756-quantum-spark-7k2x"), "quantum-spark");
  assert.equal(formatSessionName(null, "sess_2610180756-quantum-spark-7k2x"), "quantum-spark");
  assert.equal(formatSessionName(null, "2601231430-quantum-spark"), "quantum-spark");
});

test("a name people gave keeps every part", () => {
  assert.equal(formatSessionName("fix-2day"), "fix-2day");
  assert.equal(formatSessionName("2610180756-release-notes-v2"), "release-notes-v2");
  assert.equal(formatSessionName(null, null), "session");
});
