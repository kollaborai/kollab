/** Real browser transport code against a local relay; no UI or model is automated. */
import assert from "node:assert/strict";
import { createInterface } from "node:readline/promises";
import sodium from "libsodium-wrappers";
import { PhoneTransport } from "../src/phonelink/phone-transport.ts";

const input = createInterface({ input: process.stdin });
const settings = JSON.parse(
  (await input[Symbol.asyncIterator]().next()).value!,
);
input.close();
const NativeSocket = globalThis.WebSocket;
// TLS/discovery are tested separately. This local protocol oracle maps the
// directory URL to a loopback test server and uses its native WS route.
globalThis.WebSocket = class extends NativeSocket {
  constructor(_url: string | URL, protocol?: string | string[]) {
    super(settings.socket, protocol);
  }
} as typeof WebSocket;
const storage = new Map<string, string>();
Object.assign(globalThis, {
  window: globalThis,
  location: {
    protocol: "https:",
    origin: "https://relay.example",
    host: "relay.example",
    hostname: "relay.example",
    href: "https://relay.example/relay/v1/phone/",
  },
  localStorage: {
    setItem: (key: string, value: string) => storage.set(key, value),
    getItem: (key: string) => storage.get(key) ?? null,
  },
  sessionStorage: { removeItem: () => {} },
});
const states: string[] = [];
const transport = new PhoneTransport(settings.identity, (state) => {
  states.push(state);
  if (state === "approval") process.stdout.write("approval\n");
});
try {
  await transport.connect();
  const uninstall = transport.installFetch();
  const config = await (await fetch("/api/config")).json();
  assert.equal(config.engine_url, "/engine");
  assert.equal(config.token, "");
  const replies = await Promise.all(
    Array.from({ length: 7 }, async () => {
      const response = await fetch("/engine/sessions");
      assert.equal(response.status, 200);
      return response.json();
    }),
  );
  assert.ok(
    replies.every(
      (reply) => reply.authenticated && reply.sessions.length === 0,
    ),
  );
  const data = Buffer.alloc(70_000, "z");
  const uploaded = await (
    await fetch("/engine/sessions/echo", { method: "POST", body: data })
  ).json();
  assert.equal(uploaded.length, data.length);
  const response = await fetch("/engine/sessions/stream");
  assert.match(response.headers.get("content-type")!, /text\/event-stream/);
  const reader = response.body!.getReader();
  assert.equal(
    new TextDecoder().decode((await reader.read()).value),
    "data: first\n\n",
  );
  // Possessing the QR room is not local approval. A third key must not be able
  // to interrupt the approved phone, including while its stream remains open.
  const attacker = new NativeSocket(settings.socket);
  const frames: Record<string, any>[] = [];
  let waiting: ((frame: Record<string, any>) => void) | undefined;
  attacker.onmessage = (event) => {
    const frame = JSON.parse(String(event.data));
    if (waiting) {
      const resolve = waiting;
      waiting = undefined;
      resolve(frame);
    } else frames.push(frame);
  };
  const nextFrame = async (type: string) => {
    for (;;) {
      const frame = frames.shift() ?? await new Promise<Record<string, any>>(
        (resolve) => { waiting = resolve; },
      );
      assert.notEqual(frame.type, "error", JSON.stringify(frame));
      if (frame.type === type) return frame;
    }
  };
  try {
    const challenge = await nextFrame("challenge");
    const keypair = sodium.crypto_sign_keypair();
    const key = sodium.to_hex(keypair.publicKey);
    const session = sodium.to_hex(sodium.randombytes_buf(16));
    const registration = ["kollab-relay/1", "https://relay.example", challenge.nonce,
      key, settings.identity.room, session].join("\n");
    attacker.send(JSON.stringify({
      type: "register", key, room: settings.identity.room, session,
      signature: sodium.to_hex(sodium.crypto_sign_detached(
        sodium.from_string(registration), keypair.privateKey,
      )),
    }));
    await nextFrame("registered");
    attacker.send(JSON.stringify({
      type: "send",
      to: sodium.to_hex(sodium.crypto_sign_seed_keypair(
        sodium.from_hex(settings.identity.seed),
      ).publicKey),
      id: sodium.to_hex(sodium.randombytes_buf(16)),
      ciphertext: sodium.to_base64(sodium.randombytes_buf(64), sodium.base64_variants.ORIGINAL),
    }));
    // The relay has no delivery receipt. A following control response orders
    // this probe after the preceding send on the attacker's connection.
    attacker.send(JSON.stringify({
      type: "links", peers: [], issued_at: Math.floor(Date.now() / 1000),
    }));
    await nextFrame("links_result");
    const afterAttack = await (await fetch("/engine/sessions")).json();
    assert.equal(afterAttack.authenticated, true);
    assert.equal(states.includes("offline"), false, "Unapproved traffic disconnected the phone");
    sodium.memzero(keypair.privateKey);
  } finally {
    attacker.close();
  }
  await reader.cancel();
  await assert.rejects(fetch("/engine/phonelink"), /only on the computer/);
  uninstall();
  process.stdout.write(
    "passed: JS/Python encryption, approval, concurrent requests, upload, streaming, cancellation, local-only gate\n",
  );
} finally {
  transport.close();
}
