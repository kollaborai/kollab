import assert from "node:assert/strict";
import test from "node:test";
import sodium from "libsodium-wrappers";
import {
  hostBoxKey,
  importSeed,
  phoneIdentity,
  type PhoneIdentity,
} from "../src/phonelink/phone-transport.ts";

const permanentKey = "kollab.phonelink.identity.v1";
const pendingKey = "kollab.phonelink.pending.v1";
const identity = (approved: boolean, expires: number): PhoneIdentity => ({
  id: "a".repeat(32),
  room: "b".repeat(64),
  host: "c".repeat(64),
  seed: "d".repeat(64),
  approved,
  expires,
});

function storage(pending: string | null, permanent: PhoneIdentity) {
  const values = new Map([[permanentKey, JSON.stringify(permanent)]]);
  if (pending) values.set(pendingKey, pending);
  const store = {
    getItem: (key: string) => values.get(key) ?? null,
    removeItem: (key: string) => values.delete(key),
  };
  Object.assign(globalThis, {
    window: { location: { hash: "" } },
    sessionStorage: store,
    localStorage: store,
  });
  return values;
}

test("bookmark opens an approved link after an abandoned invitation expires", async () => {
  const approved = identity(true, 1);
  const values = storage(JSON.stringify(identity(false, 1)), approved);
  assert.deepEqual(await phoneIdentity(), approved);
  assert.equal(values.has(pendingKey), false);
  assert.equal(values.has(permanentKey), true);
});

test("corrupt pending state does not hide a valid permanent link", async () => {
  const approved = identity(true, 1);
  const values = storage("{corrupt", approved);
  assert.deepEqual(await phoneIdentity(), approved);
  assert.equal(values.has(pendingKey), false);
});

test("a current invitation still takes precedence while awaiting approval", async () => {
  const pending = identity(false, Math.floor(Date.now() / 1000) + 100);
  storage(JSON.stringify(pending), identity(true, 1));
  assert.deepEqual(await phoneIdentity(), pending);
});

test("an unavailable session store does not block an approved permanent link", async () => {
  const approved = identity(true, 1);
  storage(null, approved);
  Object.assign(globalThis, {
    sessionStorage: {
      getItem: () => { throw new Error("unavailable"); },
      removeItem: () => { throw new Error("unavailable"); },
    },
  });
  assert.deepEqual(await phoneIdentity(), approved);
});

test("sealed keys sign and box exactly as the seed did, and never export", async () => {
  await sodium.ready;
  const host = sodium.crypto_box_keypair();
  for (let round = 0; round < 8; round++) {
    const seed = sodium.randombytes_buf(32);
    const pair = sodium.crypto_sign_seed_keypair(seed);
    const curve = sodium.crypto_sign_ed25519_sk_to_curve25519(pair.privateKey);
    const message = sodium.randombytes_buf(100);
    const sealed = await importSeed(seed);
    assert.equal(sealed.key, sodium.to_hex(pair.publicKey));
    assert.deepEqual(
      new Uint8Array(await crypto.subtle.sign("Ed25519", sealed.sign, new Uint8Array(message))),
      sodium.crypto_sign_detached(message, pair.privateKey),
    );
    assert.deepEqual(
      await hostBoxKey(sealed.box, host.publicKey),
      sodium.crypto_box_beforenm(host.publicKey, curve),
    );
    assert.deepEqual([sealed.sign.extractable, sealed.box.extractable], [false, false]);
    await assert.rejects(crypto.subtle.exportKey("pkcs8", sealed.sign));
    await assert.rejects(crypto.subtle.exportKey("pkcs8", sealed.box));
  }
});

test("a sealed link (public key, no seed) opens as a saved link", async () => {
  const link: PhoneIdentity = {
    id: "a".repeat(32),
    room: "b".repeat(64),
    host: "c".repeat(64),
    key: "e".repeat(64),
    approved: true,
    expires: 1,
  };
  storage(null, link);
  assert.deepEqual(await phoneIdentity(), link);
});

test("a saved link with neither key nor seed is refused", async () => {
  const { seed: _seed, ...bare } = identity(true, 1);
  storage(null, bare as PhoneIdentity);
  await assert.rejects(phoneIdentity(), /saved link is invalid/);
});
