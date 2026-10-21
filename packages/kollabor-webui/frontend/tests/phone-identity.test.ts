import assert from "node:assert/strict";
import test from "node:test";
import { phoneIdentity, type PhoneIdentity } from "../src/phonelink/phone-transport.ts";

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
