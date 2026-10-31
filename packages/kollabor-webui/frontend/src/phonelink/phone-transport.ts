import sodium from "libsodium-wrappers";

import {
  decodePhoneFrame,
  encodePhoneFrame,
  PHONE_FRAME_CHUNK_BYTES,

  PHONE_RELAY_MAX_FRAME_BYTES,

  PHONE_RELAY_MAX_CIPHERTEXT_CHARS,
  supportsPhoneFrameCompression,
} from "./phone-frame.ts";


const PROTOCOL = "kollab-relay/1";
const PHONE_PROTOCOL = "kollab-phonelink/1";
const KEY = /^[0-9a-f]{64}$/;
const ID = /^[0-9a-f]{32}$/;
const STORAGE = "kollab.phonelink.identity.v1";
const PENDING = "kollab.phonelink.pending.v1";
const LEGACY_CHUNK = 8192;
const MAX_BODY = 8 * 1024 * 1024;
const MAX_PHONE_DIAGNOSTICS = 32;
const PHONE_DIAGNOSTIC_EVENTS = new Set([
  "pagehide",
  "freeze",
  "visibility_hidden",
  "visibility_visible",
  "online",
  "offline",
  "load_failure",
  "render_failure",
  "runtime_error",
  "unhandled_rejection",
  "relay_socket_opened",
  "relay_socket_closed",
  "relay_reconnecting",
  "relay_host_session_changed",
  "relay_error",
]);
type PhoneCloseReason =
  | "normal"
  | "going_away"
  | "protocol_error"
  | "unsupported_data"
  | "invalid_payload"
  | "policy_violation"
  | "message_too_big"
  | "mandatory_extension"
  | "internal_error"
  | "service_restart"
  | "try_again_later"
  | "bad_gateway"
  | "tls_handshake"
  | "application"
  | "unknown";
const PHONE_CLOSE_REASONS = new Set<PhoneCloseReason>([
  "normal",
  "going_away",
  "protocol_error",
  "unsupported_data",
  "invalid_payload",
  "policy_violation",
  "message_too_big",
  "mandatory_extension",
  "internal_error",
  "service_restart",
  "try_again_later",
  "bad_gateway",
  "tls_handshake",
  "application",
  "unknown",
]);
function closeReasonForCode(code: number): PhoneCloseReason {
  const known: Record<number, PhoneCloseReason> = {
    1000: "normal",
    1001: "going_away",
    1002: "protocol_error",
    1003: "unsupported_data",
    1007: "invalid_payload",
    1008: "policy_violation",
    1009: "message_too_big",
    1010: "mandatory_extension",
    1011: "internal_error",
    1012: "service_restart",
    1013: "try_again_later",
    1014: "bad_gateway",
    1015: "tls_handshake",
  };
  return known[code] ?? (code >= 3000 && code <= 4999 ? "application" : "unknown");
}
type PhoneDiagnostic = {
  event: string;
  close_code?: number;
  close_reason?: PhoneCloseReason;
  retry_count?: number;
};
type Json = Record<string, any>;
export type PhoneIdentity = {
  id: string;
  room: string;
  host: string;
  expires: number;
  /** The phone's Ed25519 public key; its private keys are sealed in IndexedDB. */
  key?: string;
  /** Only where the browser cannot keep sealed WebCrypto keys. */
  seed?: string;
  approved: boolean;
};
type PhoneKeys = { sign: CryptoKey; box: CryptoKey };
type Pending = {
  method: string;
  resolve: (value: Json) => void;
  reject: (error: Error) => void;
};
const join = (...parts: Uint8Array[]) => {
  const result = new Uint8Array(
    parts.reduce((sum, part) => sum + part.length, 0),
  );
  let offset = 0;
  for (const part of parts) {
    result.set(part, offset);
    offset += part.length;
  }
  return result;
};
const hex = (bytes: Uint8Array) => sodium.to_hex(bytes);
const randomId = () => hex(sodium.randombytes_buf(16));
const base64 = (bytes: Uint8Array) =>
  sodium.to_base64(bytes, sodium.base64_variants.ORIGINAL);
const unbase64 = (value: string) =>
  sodium.from_base64(value, sodium.base64_variants.ORIGINAL);
const sha256 = async (bytes: Uint8Array) =>
  new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)));
const lost = () =>
  new Error(
    "Connection to your computer was lost. An action already sent may still finish there.",
  );

/**
 * How long a connected phone keeps its relay socket open while its host is
 * absent from the roster. The engine legitimately restarts and re-registers,
 * so a briefly missing roster entry must not terminate the link.
 */
export const HOST_MISSING_GRACE_MS = 15_000;

/** What a relay peers frame means for the tracked host session. Pure so the
 * tolerance rules stay unit-testable; the transport applies it. */
export type HostSnapshotDecision =
  | { action: "lost"; why: "invalid" | "missing" }
  | { action: "present"; session: string }
  | { action: "changed"; session: string }
  | { action: "absent"; missingSinceMs: number };

export function hostSnapshotDecision(
  peers: Json,
  hostKey: string,
  knownSession: string,
  missingSinceMs: number,
  nowMs: number,
): HostSnapshotDecision {
  if (!Array.isArray(peers) || peers.length > 256)
    return { action: "lost", why: "invalid" };
  const host = peers.find((p: Json) => p.key === hostKey);
  if (host === undefined) {
    // Before the first settle the handshake requires the host to be listed;
    // afterwards a missing host is only fatal once the grace window lapses.
    if (!knownSession) return { action: "lost", why: "missing" };
    // A missingSinceMs of 0 means no clock is running yet: the first absent
    // snapshot starts it at nowMs, so the grace window can actually elapse
    // instead of comparing against the epoch and failing closed at once.
    const since = missingSinceMs || nowMs;
    return nowMs - since >= HOST_MISSING_GRACE_MS
      ? { action: "lost", why: "missing" }
      : { action: "absent", missingSinceMs: since };
  }
  if (typeof host.session !== "string" || !ID.test(host.session))
    return { action: "lost", why: "invalid" };
  // The engine's relay session changes on its own restarts and reconnects.
  // The roster row is still signed by the same host key, so the new session
  // is adopted instead of dropping the whole phone link.
  return knownSession && knownSession !== host.session
    ? { action: "changed", session: host.session }
    : { action: "present", session: host.session };
}

/** 12 characters in groups of four; the computer shows the same code. */
export function pairingCode(key: string) {
  const head = key.slice(0, 12).toUpperCase();
  return [head.slice(0, 4), head.slice(4, 8), head.slice(8, 12)].join("-");
}

/** Derived from the host key in the QR; the computer shows the same fingerprint. */
export async function hostFingerprint(hostKey: string) {
  await sodium.ready;
  const digest = hex(
    await sha256(join(sodium.from_string("kollab-phonelink-fingerprint/1\n"), sodium.from_hex(hostKey))),
  )
    .slice(0, 20)
    .toUpperCase();
  return digest.match(/.{4}/g)!.join("-");
}

function storedIdentity(raw: string): PhoneIdentity | null {
  try {
    const value = JSON.parse(raw);
    if (
      value && ID.test(value.id) && KEY.test(value.room) &&
      KEY.test(value.host) && (KEY.test(value.key) || KEY.test(value.seed)) &&
      Number.isSafeInteger(value.expires) && typeof value.approved === "boolean"
    ) return value;
  } catch { /* Invalid storage cannot replace an approved saved link. */ }
  return null;
}

// PKCS #8 headers (RFC 8410) for a 32-byte Ed25519 seed and a 32-byte X25519 key.
const ED25519_PKCS8 = "302e020100300506032b657004220420";
const X25519_PKCS8 = "302e020100300506032b656e04220420";
const KEYS = "kollab-phonelink-keys";

function keyDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open(KEYS, 1);
    open.onupgradeneeded = () => open.result.createObjectStore("keys");
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
}
async function keyStore<T>(
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await keyDatabase();
  try {
    return await new Promise<T>((resolve, reject) => {
      const transaction = db.transaction("keys", mode);
      const request = run(transaction.objectStore("keys"));
      transaction.oncomplete = () => resolve(request.result);
      transaction.onerror = transaction.onabort = () =>
        reject(transaction.error ?? request.error);
    });
  } finally {
    db.close();
  }
}

/** A seed's Ed25519 key and its X25519 twin as non-extractable WebCrypto keys:
 * the same keys on the wire, which script on this page can use but never copy out. */
export async function importSeed(
  seed: Uint8Array,
): Promise<PhoneKeys & { key: string }> {
  const pair = sodium.crypto_sign_seed_keypair(seed);
  const curve = sodium.crypto_sign_ed25519_sk_to_curve25519(pair.privateKey);
  const signDer = join(sodium.from_hex(ED25519_PKCS8), seed);
  const boxDer = join(sodium.from_hex(X25519_PKCS8), curve);
  try {
    return {
      key: hex(pair.publicKey),
      sign: await crypto.subtle.importKey("pkcs8", signDer, { name: "Ed25519" }, false, ["sign"]),
      box: await crypto.subtle.importKey("pkcs8", boxDer, { name: "X25519" }, false, ["deriveBits"]),
    };
  } finally {
    for (const secret of [pair.privateKey, curve, signDer, boxDer]) sodium.memzero(secret);
  }
}

/** HSalsa20 of a 32-byte key and a zero input: crypto_box_beforenm's last step. */
function hsalsa20(key: Uint8Array): Uint8Array {
  const view = new DataView(key.buffer, key.byteOffset, 32);
  const x = new Uint32Array(16); // x[6..9] stay 0: the zero input
  [x[0], x[5], x[10], x[15]] = [0x61707865, 0x3320646e, 0x79622d32, 0x6b206574]; // "expand 32-byte k"
  for (let i = 0; i < 4; i++) {
    x[1 + i] = view.getUint32(i * 4, true);
    x[11 + i] = view.getUint32(16 + i * 4, true);
  }
  const rotl = (value: number, shift: number) => (value << shift) | (value >>> (32 - shift));
  const quarter = (a: number, b: number, c: number, d: number) => {
    x[b] ^= rotl(x[a] + x[d], 7);
    x[c] ^= rotl(x[b] + x[a], 9);
    x[d] ^= rotl(x[c] + x[b], 13);
    x[a] ^= rotl(x[d] + x[c], 18);
  };
  for (let round = 0; round < 20; round += 2) {
    quarter(0, 4, 8, 12); quarter(5, 9, 13, 1); quarter(10, 14, 2, 6); quarter(15, 3, 7, 11);
    quarter(0, 1, 2, 3); quarter(5, 6, 7, 4); quarter(10, 11, 8, 9); quarter(15, 12, 13, 14);
  }
  const out = new Uint8Array(32);
  const write = new DataView(out.buffer);
  [0, 5, 10, 15, 6, 7, 8, 9].forEach((word, i) => write.setUint32(i * 4, x[word], true));
  x.fill(0);
  return out;
}

/** crypto_box_beforenm with the phone's WebCrypto X25519 key (the standard
 * libsodium-wrappers build does not export crypto_core_hsalsa20). */
export async function hostBoxKey(box: CryptoKey, hostCurve: Uint8Array): Promise<Uint8Array> {
  const host = await crypto.subtle.importKey("raw", new Uint8Array(hostCurve), { name: "X25519" }, false, []);
  const shared = new Uint8Array(
    await crypto.subtle.deriveBits({ name: "X25519", public: host }, box, 256),
  );
  try {
    return hsalsa20(shared);
  } finally {
    sodium.memzero(shared);
  }
}

/** A seed becomes sealed keys in IndexedDB. A browser that cannot keep them
 * (no WebCrypto Ed25519 and X25519, or no IndexedDB) keeps the seed, as before. */
async function sealed(identity: PhoneIdentity): Promise<PhoneIdentity> {
  if (!identity.seed) return identity;
  const seed = sodium.from_hex(identity.seed);
  try {
    const { key, sign, box } = await importSeed(seed);
    await keyStore("readwrite", (store) => store.put({ sign, box }, identity.id));
    return {
      id: identity.id,
      room: identity.room,
      host: identity.host,
      expires: identity.expires,
      key,
      approved: identity.approved,
    };
  } catch {
    return identity;
  } finally {
    sodium.memzero(seed);
  }
}

/** Drops the sealed keys of links this phone replaced. */
function pruneKeys(keep: string): Promise<unknown> {
  return keyStore("readwrite", (store) => {
    const request = store.openCursor();
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) return;
      if (cursor.key !== keep) cursor.delete();
      cursor.continue();
    };
    return request;
  }).catch(() => undefined);
}

export async function phoneIdentity(): Promise<PhoneIdentity> {
  await sodium.ready;
  const parameters = new URLSearchParams(window.location.hash.slice(1));
  if (parameters.has("room")) {
    // Fragments never reach the server. Remove it before mounting any app UI.
    history.replaceState(
      null,
      "",
      window.location.pathname + window.location.search,
    );
    const identity = {
      id: parameters.get("id") ?? "",
      room: parameters.get("room") ?? "",
      host: parameters.get("host") ?? "",
      expires: Number(parameters.get("expires")),
      seed: hex(sodium.randombytes_buf(32)),
      approved: false,
    };
    if (
      parameters.get("v") !== "1" ||
      !ID.test(identity.id) ||
      !KEY.test(identity.room) ||
      !KEY.test(identity.host) ||
      !Number.isSafeInteger(identity.expires) ||
      identity.expires <= Date.now() / 1000 ||
      identity.expires > Date.now() / 1000 + 330
    ) {
      throw new Error(
        "This QR code has expired or is invalid. Create a new one on your computer.",
      );
    }
    const scanned = await sealed(identity);
    try {
      sessionStorage.setItem(PENDING, JSON.stringify(scanned));
    } catch {
      /* This window still owns the invitation. */
    }
    return scanned;
  }
  let readable = false;
  let pending: string | null = null;
  try {
    pending = sessionStorage.getItem(PENDING);
    readable = true;
  } catch { /* The permanent store may still be available. */ }
  if (pending) {
    const identity = storedIdentity(pending);
    if (identity && !identity.approved && identity.expires > Date.now() / 1000)
      return identity;
    try { sessionStorage.removeItem(PENDING); } catch { /* Storage may be blocked. */ }
  }
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(STORAGE);
    readable = true;
  } catch { /* Keep the storage error actionable below. */ }
  if (!readable)
    throw new Error(
      "This browser cannot read its saved link. Scan a new QR code from your computer.",
    );
  if (!saved)
    throw new Error(
      pending
        ? "This QR code has expired or is invalid. Create a new one on your computer."
        : "Open Settings → PhoneLink on your computer and scan its QR code to link this phone.",
    );
  const identity = storedIdentity(saved);
  if (!identity || !identity.approved)
    throw new Error(
      "This browser's saved link is invalid. Scan a new QR code.",
    );
  // A link an older loader saved with its seed moves into sealed keys here.
  const ready = await sealed(identity);
  if (ready !== identity)
    try {
      localStorage.setItem(STORAGE, JSON.stringify(ready));
    } catch { /* The seed stays until a later launch can save the sealed link. */ }
  return ready;
}

/** Browser implementation of the existing session-bound NaCl relay envelopes. */
export class PhoneTransport {
  private socket: WebSocket | null = null;
  private session = "";
  private hostSession = "";
  private hostMissingSince = 0;
  private hostMissingTimer: ReturnType<typeof setTimeout> | null = null;
  private key = "";
  private signer: CryptoKey | Uint8Array | null = null;
  private hostBox: Uint8Array | null = null;
  private roomHash = "";
  private pending = new Map<string, Pending>();
  private seen = new Map<string, number>();
  private opening: Promise<void> | null = null;
  private incoming: Uint8Array | null = null;
  private outgoing: Uint8Array | null = null;

  private frameCompression = false;
  private chunkSize = LEGACY_CHUNK;
  private zeroBodyAutorun = false;
  private sent = 0;
  private received = -1;
  private active = 0;
  private slots: Array<() => void> = [];
  private sending: Promise<void> = Promise.resolve();
  private lastSend = 0;
  private closed = false;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private reconnectCount = 0;
  private diagnosticQueue: PhoneDiagnostic[] = [];
  private flushingDiagnostics: Promise<void> | null = null;
  private onState: (state: string, code?: string) => void;

  readonly identity: PhoneIdentity;

  constructor(
    identity: PhoneIdentity,
    onState: (state: string, code?: string) => void,
  ) {
    // No parameter property: Node's type-stripping test runner rejects it.
    this.identity = identity;
    this.onState = onState;
  }

  get pairingCode() {
    return pairingCode(this.key);
  }

  /** Queue fixed event codes and allowlisted transport metadata; never raw errors. */
  reportDiagnostic(
    event: string,
    fields: {
      close_code?: number;
      close_reason?: PhoneCloseReason;
      retry_count?: number;
    } = {},
  ): Promise<void> {
    if (this.closed || !PHONE_DIAGNOSTIC_EVENTS.has(event))
      return Promise.resolve();
    const payload: PhoneDiagnostic = { event };
    if (
      Number.isSafeInteger(fields.close_code) &&
      fields.close_code! >= 1000 &&
      fields.close_code! <= 4999
    )
      payload.close_code = fields.close_code;
    if (
      event === "relay_socket_closed" &&
      payload.close_code !== undefined &&
      typeof fields.close_reason === "string" &&
      PHONE_CLOSE_REASONS.has(fields.close_reason)
    )
      payload.close_reason = fields.close_reason;
    if (
      Number.isSafeInteger(fields.retry_count) &&
      fields.retry_count! >= 0 &&
      fields.retry_count! <= 10_000
    )
      payload.retry_count = fields.retry_count;
    const last = this.diagnosticQueue[this.diagnosticQueue.length - 1];
    if (
      last?.event === payload.event &&
      last.close_code === payload.close_code &&
      last.close_reason === payload.close_reason &&
      last.retry_count === payload.retry_count
    )
      return Promise.resolve();
    if (this.diagnosticQueue.length >= MAX_PHONE_DIAGNOSTICS)
      this.diagnosticQueue.shift();
    this.diagnosticQueue.push(payload);
    return this.flushDiagnostics();
  }

  private flushDiagnostics(): Promise<void> {
    if (this.flushingDiagnostics) return this.flushingDiagnostics;
    if (
      this.closed ||
      !this.outgoing ||
      !this.incoming ||
      this.socket?.readyState !== WebSocket.OPEN ||
      this.diagnosticQueue.length === 0
    )
      return Promise.resolve();
    let failed = false;
    const flush = async () => {
      try {
        while (
          !this.closed &&
          this.outgoing &&
          this.incoming &&
          this.socket?.readyState === WebSocket.OPEN &&
          this.diagnosticQueue.length > 0
        ) {
          const payload = this.diagnosticQueue[0]!;
          try {
            await this.request("phone_diagnostic", payload, undefined, 5000);
          } catch {
            failed = true;
            break;
          }
          // A full queue may have dropped the head while it was sent.
          if (this.diagnosticQueue[0] === payload) this.diagnosticQueue.shift();
        }
      } finally {
        this.flushingDiagnostics = null;
        if (
          !failed &&
          this.diagnosticQueue.length > 0 &&
          !this.closed &&
          this.outgoing &&
          this.incoming &&
          this.socket?.readyState === WebSocket.OPEN
        )
          void this.flushDiagnostics();
      }
    };
    this.flushingDiagnostics = flush();
    return this.flushingDiagnostics;
  }

  private armHostMissingTimer(sinceMs: number) {
    const remaining = Math.max(HOST_MISSING_GRACE_MS - (Date.now() - sinceMs), 0);
    this.clearHostMissingTimer();
    this.hostMissingTimer = setTimeout(() => {
      this.hostMissingTimer = null;
      // Still missing after the grace window: run the normal socket-close
      // path so the phone reports offline and reconnects on its own.
      if (this.hostMissingSince) this.socket?.close();
    }, remaining);
  }

  private clearHostMissingTimer() {
    if (this.hostMissingTimer) {
      clearTimeout(this.hostMissingTimer);
      this.hostMissingTimer = null;
    }
  }

  connect(): Promise<void> {
    if (this.outgoing && this.socket?.readyState === WebSocket.OPEN)
      return Promise.resolve();
    if (this.opening) return this.opening;
    this.opening = this.open()
      .catch((error) => {
        this.reportDiagnostic("relay_error");
        this.socket?.close();
        throw error;
      })
      .finally(() => {
        this.opening = null;
      });
    return this.opening;
  }

  /** This link's keys: sealed WebCrypto keys, or the seed where the browser has none. */
  private async loadKeys() {
    const host = new Uint8Array(
      sodium.crypto_sign_ed25519_pk_to_curve25519(sodium.from_hex(this.identity.host)),
    );
    if (this.identity.seed) {
      const pair = sodium.crypto_sign_seed_keypair(sodium.from_hex(this.identity.seed));
      const curve = sodium.crypto_sign_ed25519_sk_to_curve25519(pair.privateKey);
      this.key = hex(pair.publicKey);
      this.signer = pair.privateKey;
      this.hostBox = sodium.crypto_box_beforenm(host, curve);
      sodium.memzero(curve);
      return;
    }
    const keys = await keyStore<PhoneKeys | undefined>("readonly", (store) =>
      store.get(this.identity.id),
    ).catch(() => undefined);
    if (!keys || !this.identity.key)
      throw new Error("This browser's saved link is invalid. Scan a new QR code.");
    this.key = this.identity.key;
    this.signer = keys.sign;
    this.hostBox = await hostBoxKey(keys.box, host);
  }

  private async sign(message: Uint8Array) {
    const signer = this.signer;
    if (!signer) throw lost();
    return signer instanceof Uint8Array
      ? sodium.crypto_sign_detached(message, signer)
      : new Uint8Array(await crypto.subtle.sign("Ed25519", signer, new Uint8Array(message)));
  }

  private async open() {
    await sodium.ready;
    if (!this.hostBox) await this.loadKeys();
    if (this.closed) throw lost();
    this.roomHash = hex(await sha256(sodium.from_hex(this.identity.room)));
    this.session = randomId();
    this.hostSession = "";
    this.clearHostMissingTimer();
    this.hostMissingSince = 0;
    this.onState("connecting", this.pairingCode);
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    if (
      location.protocol !== "https:" &&
      !["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)
    )
      throw new Error("PhoneLink needs a secure HTTPS connection.");
    // The native protocol name contains '/', which browsers forbid in a
    // Sec-WebSocket-Protocol token. Validate it in the signed challenge instead.
    const socket = new WebSocket(
      `${scheme}//${location.host}/relay/v1/phone/ws`,
    );
    this.socket = socket;
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (!settled) {
          settled = true;
          reject(lost());
          socket.close();
        }
      }, 15000);
      socket.onmessage = (event) => {
        try {
          if (typeof event.data !== "string" || event.data.length > PHONE_RELAY_MAX_FRAME_BYTES)
            throw lost();
          const frame = JSON.parse(event.data);
          if (frame.type === "challenge") {
            if (
              frame.protocol !== PROTOCOL ||
              frame.origin !== location.origin ||
              !KEY.test(frame.nonce)
            )
              throw lost();
            const key = this.key;
            const message = [
              PROTOCOL,
              location.origin,
              frame.nonce,
              key,
              this.identity.room,
              this.session,
            ].join("\n");
            void this.sign(sodium.from_string(message))
              .then((signature) =>
                socket.send(
                  JSON.stringify({
                    type: "register",
                    key,
                    room: this.identity.room,
                    session: this.session,
                    signature: hex(signature),
                  }),
                ),
              )
              .catch(() => socket.close());
          } else if (frame.type === "registered") {
            if (
              frame.protocol !== PROTOCOL ||
              frame.key !== this.key ||
              frame.session !== this.session
            )
              throw lost();
          } else if (frame.type === "peers") {
            const decision = hostSnapshotDecision(
              frame.peers,
              this.identity.host,
              this.hostSession,
              this.hostMissingSince,
              Date.now(),
            );
            if (decision.action === "lost") throw lost();
            this.clearHostMissingTimer();
            if (decision.action === "absent") {
              // Host gone from the roster: hold the link for the grace
              // window, then let the timer's close run the normal
              // reconnect path if the host never came back.
              this.hostMissingSince = decision.missingSinceMs;
              this.armHostMissingTimer(decision.missingSinceMs);
            } else {
              this.hostMissingSince = 0;
              if (decision.action === "changed")
                this.reportDiagnostic("relay_host_session_changed");
              this.hostSession = decision.session;
            }
            if (!settled) {
              settled = true;
              clearTimeout(timer);
              resolve();
            }
          } else if (frame.type === "message") {
            try {
              this.receive(frame);
            } catch {
              // Invalid ciphertext, stale packets and replays are dropped;
              // they cannot terminate the approved host's connection.
            }
          } else if (frame.type === "error") {
            const request = this.pending.get(frame.id);
            if (request) request.reject(lost());
            else if (!settled) throw lost();
          }
        } catch {
          clearTimeout(timer);
          if (!settled) {
            settled = true;
            reject(lost());
          }
          socket.close();
        }
      };
      socket.onerror = () => {
        this.reportDiagnostic("relay_error");
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          reject(lost());
        }
      };
      socket.onclose = (event) => {
        clearTimeout(timer);
        if (!settled) {
          settled = true;
          reject(lost());
        }
        if (this.socket !== socket) return;
        this.reportDiagnostic("relay_socket_closed", {
          close_code: event.code,
          close_reason: closeReasonForCode(event.code),
        });
        this.clearHostMissingTimer();
        this.hostMissingSince = 0;
        this.incoming = this.outgoing = null;

        this.frameCompression = false;
        this.chunkSize = LEGACY_CHUNK;
        this.zeroBodyAutorun = false;
        for (const request of this.pending.values()) request.reject(lost());
        this.pending.clear();
        if (!this.closed) {
          this.onState("offline", this.pairingCode);
          if (this.identity.approved && !this.retry) {
            this.reconnectCount += 1;
            this.reportDiagnostic("relay_reconnecting", {
              retry_count: this.reconnectCount,
            });
            this.retry = setTimeout(() => {
              this.retry = null;
              void this.connect().catch(() => undefined);
            }, 3000);
          }
        }
      };
    });
    const ephemeral = sodium.crypto_box_keypair();
    let response: Json;
    for (;;) {
      if (!this.identity.approved && this.identity.expires < Date.now() / 1000)
        throw new Error(
          "This QR code expired before approval. Create a new one on your computer.",
        );
      this.onState(
        this.identity.approved ? "connecting" : "approval",
        this.pairingCode,
      );
      try {
        response = await this.request(
          "phone_session",
          {
            public_key: hex(ephemeral.publicKey),
            frame_compression: supportsPhoneFrameCompression(),
            max_chunk: PHONE_FRAME_CHUNK_BYTES,

            coalesce_reads: true,
            zero_body_autorun: true,
          },
          undefined,
          this.identity.approved ? 15000 : 3000,
        );
        break;
      } catch (error) {
        if (
          this.identity.approved ||
          socket.readyState !== WebSocket.OPEN ||
          this.closed
        )
          throw error;
      }
    }
    if (response.protocol !== PHONE_PROTOCOL || !KEY.test(response.public_key))
      throw lost();

    this.frameCompression =
      supportsPhoneFrameCompression() && response.frame_compression === true;
    this.chunkSize =
      Number.isSafeInteger(response.chunk_size) &&
      response.chunk_size >= LEGACY_CHUNK &&
      response.chunk_size <= PHONE_FRAME_CHUNK_BYTES
        ? response.chunk_size
        : LEGACY_CHUNK;
    this.zeroBodyAutorun = response.zero_body_autorun === true;
    const shared = sodium.crypto_box_beforenm(
      sodium.from_hex(response.public_key),
      ephemeral.privateKey,
    );
    const binding = join(
      sodium.from_string(PHONE_PROTOCOL),
      sodium.from_hex(this.hostSession + this.session),
    );
    this.outgoing = await sha256(
      join(shared, binding, sodium.from_string("phone-to-host")),
    );
    this.incoming = await sha256(
      join(shared, binding, sodium.from_string("host-to-phone")),
    );
    sodium.memzero(shared);
    sodium.memzero(ephemeral.privateKey);
    this.sent = 0;
    this.received = -1;
    this.identity.approved = true;
    this.reconnectCount = 0;
    this.reportDiagnostic("relay_socket_opened");
    let saved = true;
    try {
      localStorage.setItem(STORAGE, JSON.stringify(this.identity));
      sessionStorage.removeItem(PENDING);
    } catch {
      saved = false;
    }
    if (saved) void pruneKeys(this.identity.id);
    this.onState(saved ? "connected" : "temporary", this.pairingCode);
  }

  private receive(frame: Json) {
    if (frame.from !== this.identity.host) return;
    if (
      frame.session !== this.hostSession ||
      !ID.test(frame.id) ||
      typeof frame.ciphertext !== "string" ||
      frame.ciphertext.length > PHONE_RELAY_MAX_CIPHERTEXT_CHARS
    )
      throw lost();
    const encrypted = unbase64(frame.ciphertext);
    const plaintext = sodium.crypto_box_open_easy_afternm(
      encrypted.slice(24),
      encrypted.slice(0, 24),
      this.hostBox as Uint8Array,
    );
    const body = JSON.parse(sodium.to_string(plaintext));
    const now = Math.floor(Date.now() / 1000);
    if (
      body.v !== 1 ||
      body.from !== this.identity.host ||
      body.to !== this.key ||
      body.from_session !== this.hostSession ||
      body.to_session !== this.session ||
      body.room !== this.roomHash ||
      body.id !== frame.id ||
      !Number.isSafeInteger(body.sent_at) ||
      !Number.isSafeInteger(body.expires_at) ||
      body.sent_at > now + 30 ||
      body.expires_at < now ||
      body.expires_at <= body.sent_at ||
      body.expires_at > body.sent_at + 60
    )
      throw lost();
    for (const [id, expiry] of this.seen)
      if (expiry < now) this.seen.delete(id);
    if (this.seen.has(body.id) || this.seen.size >= 4096) throw lost();
    this.seen.set(body.id, body.expires_at);
    const payload = body.payload;
    if (body.kind !== "response" || !payload) throw lost();
    const pending = this.pending.get(payload.reply_to);
    if (!pending || payload.method !== pending.method) return;
    if (payload.error)
      pending.reject(
        new Error(
          "Your computer refused this app request. Check PhoneLink there.",
        ),
      );
    else if (payload.result && typeof payload.result === "object")
      pending.resolve(payload.result);
    else pending.reject(lost());
  }

  private async send(kind: string, payload: Json, id: string) {
    const now = Math.floor(Date.now() / 1000);
    const body = {
      v: 1,
      from: this.key,
      to: this.identity.host,
      from_session: this.session,
      to_session: this.hostSession,
      room: this.roomHash,
      id,
      sent_at: now,
      expires_at: now + 30,
      kind,
      payload,
    };
    const nonce = sodium.randombytes_buf(24);
    const ciphertext = base64(
      join(
        nonce,
        sodium.crypto_box_easy_afternm(
          sodium.from_string(JSON.stringify(body)),
          nonce,
          this.hostBox as Uint8Array,
        ),
      ),
    );
    const socket = this.socket;
    const job = this.sending
      .catch(() => undefined)
      .then(async () => {
        const delay = Math.max(0, 130 - (performance.now() - this.lastSend));
        if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
        if (socket !== this.socket || socket?.readyState !== WebSocket.OPEN)
          throw lost();
        const frame = JSON.stringify({
          type: "send",
          to: this.identity.host,
          id,
          ciphertext,
        });
        if (frame.length > PHONE_RELAY_MAX_FRAME_BYTES)
          throw new Error("PhoneLink relay frame exceeds its 512 KiB limit.");
        socket.send(frame);
        this.lastSend = performance.now();
      });
    this.sending = job;
    return job;
  }

  private async request(
    method: string,
    arguments_: Json,
    signal?: AbortSignal,
    timeout = 30000,
  ): Promise<Json> {
    if (this.active >= 3)
      await new Promise<void>((resolve) => this.slots.push(resolve));
    else this.active += 1;
    const id = randomId();
    let timer: ReturnType<typeof setTimeout>;
    let abort: () => void = () => {};
    try {
      if (signal?.aborted || this.closed) throw signal?.reason ?? lost();
      return await new Promise<Json>((resolve, reject) => {
        this.pending.set(id, { method, resolve, reject });
        abort = () => {
          reject(signal?.reason ?? new DOMException("Aborted", "AbortError"));
          void this.send(
            "request_cancel",
            { reply_to: id, method },
            randomId(),
          ).catch(() => undefined);
        };
        signal?.addEventListener("abort", abort, { once: true });
        timer = setTimeout(() => {
          reject(lost());
          void this.send(
            "request_cancel",
            { reply_to: id, method },
            randomId(),
          ).catch(() => undefined);
        }, timeout);
        void this.send(
          "request",
          { method, arguments: arguments_, timeout_ms: timeout },
          id,
        ).catch(reject);
      });
    } finally {
      clearTimeout(timer!);
      signal?.removeEventListener("abort", abort);
      this.pending.delete(id);
      const next = this.slots.shift();
      if (next) next();
      else this.active -= 1;
    }
  }

  private async packet(data: Json, signal?: AbortSignal) {
    if (!this.outgoing || !this.incoming) throw lost();
    const nonce = sodium.randombytes_buf(24);
    const encoded = await encodePhoneFrame(
      sodium.from_string(JSON.stringify({ ...data, seq: this.sent++ })),
      this.frameCompression,
    );
    const response = await this.request(
      "phone_http",
      {
        packet: base64(
          join(
            nonce,
            sodium.crypto_secretbox_easy(encoded, nonce, this.outgoing),
          ),
        ),
      },
      signal,
    );
    const raw = unbase64(response.packet);
    const decrypted = sodium.crypto_secretbox_open_easy(
      raw.slice(24),
      raw.slice(0, 24),
      this.incoming,
    );
    const decoded = await decodePhoneFrame(decrypted, this.frameCompression);
    const result = JSON.parse(sodium.to_string(decoded));
    if (!Number.isSafeInteger(result.seq) || result.seq <= this.received)
      throw lost();
    this.received = result.seq;
    if (result.error) throw new Error(result.error);
    return result as Json;
  }

  async fetch(path: string, init: RequestInit = {}): Promise<Response> {
    await this.connect();
    const request = new Request(new URL(path, location.origin), init);
    const body = new Uint8Array(await request.arrayBuffer());
    if (body.length > MAX_BODY)
      throw new Error("PhoneLink uploads are limited to 8 MB.");
    const id = randomId();
    const cancel = () => {
      void this.packet({ op: "cancel", id }).catch(() => undefined);
    };
    const signal = init.signal ?? undefined;
    signal?.addEventListener("abort", cancel, { once: true });
    try {
      await this.packet(
        {
          op: "start",
          id,
          method: request.method,
          path,
          headers: Array.from(request.headers),
          size: body.length,
        },
        signal,
      );
      for (let offset = 0; offset < body.length; offset += this.chunkSize)
        await this.packet(
          {
            op: "write",
            id,
            offset,
            data: base64(body.slice(offset, offset + this.chunkSize)),
          },
          signal,
        );
      if (body.length > 0 || !this.zeroBodyAutorun)
        await this.packet({ op: "run", id }, signal);
      let first: Json;
      do {
        first = await this.packet({ op: "read", id }, signal);
      } while (first.type === "waiting");
      if (first.type !== "headers") throw lost();
      const transport = this;
      const cleanup = () => signal?.removeEventListener("abort", cancel);
      const initialData = typeof first.data === "string" ? unbase64(first.data) : null;
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          if (initialData?.byteLength) controller.enqueue(initialData);
          if (first.end === true) {
            cleanup();
            controller.close();
          }
        },
        async pull(controller) {
          try {
            let event: Json;
            do {
              event = await transport.packet({ op: "read", id }, signal);
            } while (event.type === "waiting");
            if (event.type === "data") {
              controller.enqueue(unbase64(event.data));
              if (event.end === true) {
                cleanup();
                controller.close();
              }
            } else if (event.type === "end") {
              cleanup();
              controller.close();
            } else throw new Error(event.error ?? "The app request failed.");
          } catch (error) {
            cleanup();
            cancel();
            controller.error(error);
          }
        },
        cancel() {
          cleanup();
          cancel();
        },
      });
      if ([204, 205, 304].includes(first.status)) {
        await stream.cancel();
        return new Response(null, {
          status: first.status,
          headers: first.headers,
        });
      }
      return new Response(stream, {
        status: first.status,
        headers: first.headers,
      });
    } catch (error) {
      signal?.removeEventListener("abort", cancel);
      cancel();
      throw error;
    }
  }

  /** The assistant transport also uses fetch; intercept only this app's engine paths. */
  installFetch(): () => void {
    const original = window.fetch.bind(window);
    const replacement: typeof fetch = async (input, init) => {
      const request =
        input instanceof Request
          ? new Request(input, init)
          : new Request(new URL(String(input), location.href), init);
      const url = new URL(request.url);
      if (url.origin === location.origin && url.pathname === "/api/config")
        return Response.json(
          { engine_url: "/engine", token: "" },
          { headers: { "Cache-Control": "no-store" } },
        );
      if (
        url.origin === location.origin &&
        url.pathname.startsWith("/engine/")
      ) {
        const body = ["GET", "HEAD"].includes(request.method)
          ? undefined
          : await request.arrayBuffer();
        return this.fetch(url.pathname.slice(7) + url.search, {
          method: request.method,
          headers: request.headers,
          body,
          signal: request.signal,
        });
      }
      return original(request);
    };
    window.fetch = replacement;
    return () => {
      if (window.fetch === replacement) window.fetch = original;
    };
  }

  close() {
    this.closed = true;
    this.diagnosticQueue = [];
    if (this.retry) clearTimeout(this.retry);
    this.clearHostMissingTimer();
    this.hostMissingSince = 0;
    this.socket?.close();
    for (const key of [
      this.hostBox,
      this.incoming,
      this.outgoing,
      this.signer instanceof Uint8Array ? this.signer : null,
    ])
      if (key) sodium.memzero(key);
  }
}
