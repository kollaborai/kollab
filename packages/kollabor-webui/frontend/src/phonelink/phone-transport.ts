import sodium from "libsodium-wrappers";

import {
  decodePhoneFrame,
  encodePhoneFrame,
  PHONE_FRAME_CHUNK_BYTES,

  PHONE_RELAY_MAX_FRAME_BYTES,

  PHONE_RELAY_MAX_CIPHERTEXT_CHARS,
  supportsPhoneFrameCompression,
} from "./phone-frame";


const PROTOCOL = "kollab-relay/1";
const PHONE_PROTOCOL = "kollab-phonelink/1";
const KEY = /^[0-9a-f]{64}$/;
const ID = /^[0-9a-f]{32}$/;
const STORAGE = "kollab.phonelink.identity.v1";
const PENDING = "kollab.phonelink.pending.v1";
const LEGACY_CHUNK = 8192;
const MAX_BODY = 8 * 1024 * 1024;
type Json = Record<string, any>;
export type PhoneIdentity = {
  id: string;
  room: string;
  host: string;
  expires: number;
  seed: string;
  approved: boolean;
};
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
      KEY.test(value.host) && KEY.test(value.seed) &&
      Number.isSafeInteger(value.expires) && typeof value.approved === "boolean"
    ) return value;
  } catch { /* Invalid storage cannot replace an approved saved link. */ }
  return null;
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
    try {
      sessionStorage.setItem(PENDING, JSON.stringify(identity));
    } catch {
      /* This window still owns the invitation. */
    }
    return identity;
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
  return identity;
}

/** Browser implementation of the existing session-bound NaCl relay envelopes. */
export class PhoneTransport {
  private socket: WebSocket | null = null;
  private session = "";
  private hostSession = "";
  private keypair!: { publicKey: Uint8Array; privateKey: Uint8Array };
  private curvePrivate!: Uint8Array;
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
    return pairingCode(hex(this.keypair.publicKey));
  }

  connect(): Promise<void> {
    if (this.outgoing && this.socket?.readyState === WebSocket.OPEN)
      return Promise.resolve();
    if (this.opening) return this.opening;
    this.opening = this.open()
      .catch((error) => {
        this.socket?.close();
        throw error;
      })
      .finally(() => {
        this.opening = null;
      });
    return this.opening;
  }

  private async open() {
    await sodium.ready;
    if (this.closed) throw lost();
    this.keypair = sodium.crypto_sign_seed_keypair(
      sodium.from_hex(this.identity.seed),
    );
    this.curvePrivate = sodium.crypto_sign_ed25519_sk_to_curve25519(
      this.keypair.privateKey,
    );
    this.roomHash = hex(await sha256(sodium.from_hex(this.identity.room)));
    this.session = randomId();
    this.hostSession = "";
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
            const key = hex(this.keypair.publicKey);
            const message = [
              PROTOCOL,
              location.origin,
              frame.nonce,
              key,
              this.identity.room,
              this.session,
            ].join("\n");
            socket.send(
              JSON.stringify({
                type: "register",
                key,
                room: this.identity.room,
                session: this.session,
                signature: hex(
                  sodium.crypto_sign_detached(
                    sodium.from_string(message),
                    this.keypair.privateKey,
                  ),
                ),
              }),
            );
          } else if (frame.type === "registered") {
            if (
              frame.protocol !== PROTOCOL ||
              frame.key !== hex(this.keypair.publicKey) ||
              frame.session !== this.session
            )
              throw lost();
          } else if (frame.type === "peers") {
            if (!Array.isArray(frame.peers) || frame.peers.length > 256)
              throw lost();
            const host = frame.peers.find(
              (p: Json) => p.key === this.identity.host,
            );
            if (!host || !ID.test(host.session)) throw lost();
            if (this.hostSession && this.hostSession !== host.session)
              throw lost();
            this.hostSession = host.session;
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
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          reject(lost());
        }
      };
      socket.onclose = () => {
        clearTimeout(timer);
        if (!settled) {
          settled = true;
          reject(lost());
        }
        if (this.socket !== socket) return;
        this.incoming = this.outgoing = null;

        this.frameCompression = false;
        this.chunkSize = LEGACY_CHUNK;
        this.zeroBodyAutorun = false;
        for (const request of this.pending.values()) request.reject(lost());
        this.pending.clear();
        if (!this.closed) {
          this.onState("offline", this.pairingCode);
          if (this.identity.approved && !this.retry)
            this.retry = setTimeout(() => {
              this.retry = null;
              void this.connect().catch(() => undefined);
            }, 3000);
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
    let saved = true;
    try {
      localStorage.setItem(STORAGE, JSON.stringify(this.identity));
      sessionStorage.removeItem(PENDING);
    } catch {
      saved = false;
    }
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
    const plaintext = sodium.crypto_box_open_easy(
      encrypted.slice(24),
      encrypted.slice(0, 24),
      sodium.crypto_sign_ed25519_pk_to_curve25519(
        sodium.from_hex(this.identity.host),
      ),
      this.curvePrivate,
    );
    const body = JSON.parse(sodium.to_string(plaintext));
    const now = Math.floor(Date.now() / 1000);
    if (
      body.v !== 1 ||
      body.from !== this.identity.host ||
      body.to !== hex(this.keypair.publicKey) ||
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
      from: hex(this.keypair.publicKey),
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
        sodium.crypto_box_easy(
          sodium.from_string(JSON.stringify(body)),
          nonce,
          sodium.crypto_sign_ed25519_pk_to_curve25519(
            sodium.from_hex(this.identity.host),
          ),
          this.curvePrivate,
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
    if (this.retry) clearTimeout(this.retry);
    this.socket?.close();
    for (const key of [
      this.curvePrivate,
      this.incoming,
      this.outgoing,
      this.keypair?.privateKey,
    ])
      if (key) sodium.memzero(key);
  }
}
