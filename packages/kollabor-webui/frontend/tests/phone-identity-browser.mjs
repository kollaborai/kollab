// Exercise the real browser's WebCrypto and IndexedDB, including a page reload.
// node tests/phone-identity-browser.mjs [chromium|webkit]
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { build } from "vite";
import { chromium, webkit } from "playwright-core";

const browserName = process.argv[2] ?? "chromium";
assert.ok(["chromium", "webkit"].includes(browserName));
const bundle = await build({
  configFile: false,
  logLevel: "error",
  build: {
    write: false,
    minify: false,
    target: "esnext",
    lib: {
      entry: "src/phonelink/phone-transport.ts",
      formats: ["iife"],
      name: "PhoneProbe",
    },
  },
});
const code = (Array.isArray(bundle) ? bundle : [bundle])
  .flatMap((result) => result.output)
  .find((item) => item.type === "chunk").code;
const server = createServer((request, response) => {
  response.setHeader("Content-Type", request.url === "/probe.js" ? "text/javascript" : "text/html");
  response.end(request.url === "/probe.js" ? code : '<!doctype html><script src="/probe.js"></script>');
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await ({ chromium, webkit })[browserName].launch({
    headless: true,
    executablePath: browserName === "webkit" ? process.env.WEBKIT_PATH : process.env.CHROME_PATH,
  });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const fresh = await page.evaluate(async () => {
    const host = await PhoneProbe.importSeed(crypto.getRandomValues(new Uint8Array(32)));
    history.replaceState(null, "", location.pathname + "#" + new URLSearchParams({
      v: "1", id: "a".repeat(32), room: "b".repeat(64), host: host.key,
      expires: String(Math.floor(Date.now() / 1000) + 300),
    }));
    localStorage.setItem("kollab.phonelink.identity.v1", '{"broken":true}');
    const identity = await PhoneProbe.phoneIdentity();
    const transport = new PhoneProbe.PhoneTransport(identity, () => {});
    await transport.loadKeys();
    localStorage.setItem("kollab.phonelink.identity.v1", JSON.stringify({ ...identity, approved: true }));
    sessionStorage.clear();
    return { seed: !!identity.seed, key: !!identity.key, fragmentRemoved: location.hash === "" };
  });
  assert.equal(fresh.fragmentRemoved, true);
  assert.notEqual(fresh.seed, fresh.key, "retain exactly one usable key representation");
  if (browserName === "chromium") assert.equal(fresh.key, true, "working key storage still seals the seed");
  console.log(`${browserName}: fresh invitation overrides corrupt saved state; usable ${fresh.key ? "sealed keys" : "seed fallback"}`);
  await page.reload();
  const restored = await page.evaluate(async () => {
    const identity = await PhoneProbe.phoneIdentity();
    const transport = new PhoneProbe.PhoneTransport(identity, () => {});
    await transport.loadKeys();
    const message = new Uint8Array([1, 2, 3]);
    const signatureBytes = (await transport.sign(message)).byteLength;
    // loadKeys derives the actual host box key for both representations.
    return {
      seed: !!identity.seed,
      key: !!identity.key,
      signatureBytes,
      extractable: transport.signer instanceof CryptoKey ? transport.signer.extractable : null,
      boxBytes: transport.hostBox.byteLength,
    };
  });
  assert.deepEqual(restored, { seed: fresh.seed, key: fresh.key, signatureBytes: 64, extractable: fresh.key ? false : null, boxBytes: 32 });
  console.log(`${browserName}: reload restores the link and derives its host box key`);
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
