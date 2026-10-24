import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import sodium from "libsodium-wrappers";
import { AppRefused, verifyFile, verifyManifest } from "../src/phonelink/app-verify.ts";
import { hostFingerprint, pairingCode } from "../src/phonelink/phone-transport.ts";

const sha = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
const PROTOCOL = "kollab-phonelink-app/1";

async function signed(
  overrides: { serial?: number; files?: Uint8Array[]; keys?: ReturnType<typeof sodium.crypto_sign_keypair> } = {},
) {
  await sodium.ready;
  const keys = overrides.keys ?? sodium.crypto_sign_keypair();
  const host = sodium.to_hex(keys.publicKey);
  const bodies = overrides.files ?? [sodium.from_string("app()"), sodium.from_string("body{}")];
  const names = ["phone-app.js.gz", "phone-app.css"];
  const files = bodies.map((b, i) => ({ name: names[i], sha256: sha(b), size: b.length }));
  const serial = overrides.serial ?? 100;
  const bundle = sha(
    sodium.from_string(`${serial}\n` + files.map((f) => `${f.name} ${f.sha256} ${f.size}`).join("\n")),
  );
  const signature = sodium.to_hex(
    sodium.crypto_sign_detached(sodium.from_string(`${PROTOCOL}\n${host}\n${bundle}`), keys.privateKey),
  );
  return { host, bodies, keys, manifest: { protocol: PROTOCOL, serial, files, bundle, host, signature } };
}

test("a manifest signed by the QR host key is accepted and its files verify", async () => {
  const { host, manifest, bodies } = await signed();
  const accepted = await verifyManifest(structuredClone(manifest), host);
  await verifyFile(accepted, "phone-app.js.gz", bodies[0]);
  await verifyFile(accepted, "phone-app.css", bodies[1]);
});

test("a tampered file is refused before anything runs", async () => {
  const { host, manifest } = await signed();
  const accepted = await verifyManifest(structuredClone(manifest), host);
  await assert.rejects(verifyFile(accepted, "phone-app.js.gz", sodium.from_string("evil()")), AppRefused);
  await assert.rejects(verifyFile(accepted, "other.js", sodium.from_string("app()")), AppRefused);
});

test("a tampered file list or serial breaks the signature", async () => {
  const { host, manifest } = await signed();
  const swapped = structuredClone(manifest);
  swapped.files[0].sha256 = sha(sodium.from_string("evil()"));
  await assert.rejects(verifyManifest(swapped, host), AppRefused);
  const reserial = structuredClone(manifest);
  reserial.serial += 1;
  await assert.rejects(verifyManifest(reserial, host), AppRefused);
});

test("a manifest the relay re-signs with its own key is refused", async () => {
  const { host, manifest } = await signed();
  const forged = await signed({ keys: sodium.crypto_sign_keypair() });
  await assert.rejects(verifyManifest(forged.manifest, host), AppRefused);
  const claimed = structuredClone(forged.manifest);
  claimed.host = host;
  await assert.rejects(verifyManifest(claimed, host), AppRefused);
  assert.ok(await verifyManifest(structuredClone(manifest), host));
});

test("an older signed app is refused after a newer one was installed", async () => {
  const { host, manifest } = await signed({ serial: 50 });
  await assert.rejects(verifyManifest(manifest, host, 51), AppRefused);
  assert.ok(await verifyManifest(manifest, host, 50));
});

test("malformed manifests are refused", async () => {
  const { host, manifest } = await signed();
  for (const bad of [null, {}, { ...manifest, protocol: "x" }, { ...manifest, files: [] }, { ...manifest, signature: "zz" }])
    await assert.rejects(verifyManifest(bad, host), AppRefused);
});

test("pairing codes and fingerprints are fixed-format", async () => {
  assert.equal(pairingCode("abcdef0123456789".padEnd(64, "0")), "ABCD-EF01-2345");
  await sodium.ready;
  assert.match(await hostFingerprint("c".repeat(64)), /^[0-9A-F]{4}(-[0-9A-F]{4}){4}$/);
});
