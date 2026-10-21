import assert from "node:assert/strict";
import test from "node:test";
import { copyText } from "../src/phonelink/copy-link.ts";

const link = "https://x.example/relay/v1/phone/#v=1&id=a&room=b&host=c&expires=1";

test("copyText writes the exact invitation link and reports success", async () => {
  const written: string[] = [];
  assert.equal(await copyText({ writeText: async (t) => void written.push(t) }, link), true);
  assert.deepEqual(written, [link]);
});

test("copyText reports failure when the clipboard is missing or rejects", async () => {
  assert.equal(await copyText(undefined, link), false);
  assert.equal(
    await copyText({ writeText: () => Promise.reject(new Error("denied")) }, link),
    false,
  );
});
