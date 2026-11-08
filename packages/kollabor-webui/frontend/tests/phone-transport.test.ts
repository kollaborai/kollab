import { describe, expect, it, vi } from "vitest";
import sodium from "libsodium-wrappers";
import {
  hostSnapshotDecision,
  HOST_MISSING_GRACE_MS,
  WireSequence,
  ReplySequenceWindow,
  PhoneTransport,
  RequestSlots,
} from "../src/phonelink/phone-transport";
import { encodePhoneFrame } from "../src/phonelink/phone-frame";

const HOST = "a".repeat(64);
const OLD_SESSION = "1".repeat(32);
const NEW_SESSION = "2".repeat(32);
const NOW = 1_000_000;

it.each(["call_open", "call_close"] as const)("keeps %s alive beyond a three-second bridge operation", async (op) => {
  await sodium.ready;
  vi.useFakeTimers();
  const transport = new PhoneTransport({} as ConstructorParameters<typeof PhoneTransport>[0], () => {});
  const internal = transport as unknown as {
    packet: (packet: Record<string, unknown>, signal?: AbortSignal, lane?: "call" | "normal", timeout?: number) => Promise<Record<string, unknown>>;
    request: (method: string, packet: Record<string, unknown>, signal: AbortSignal | undefined, timeout: number, lane: "call" | "normal") => Promise<Record<string, unknown>>;
    send: (...args: unknown[]) => Promise<void>;
  };
  vi.spyOn(internal, "send").mockResolvedValue();
  vi.spyOn(internal, "packet").mockImplementation((packet, signal, lane, timeout) =>
    internal.request("phone_http", packet, signal, timeout!, lane!));
  const abort = new AbortController();
  let settled = false;
  const pending = transport.callPacket({ op, id: "a".repeat(32) }, abort.signal).finally(() => { settled = true; });
  const observed = pending.catch((error) => error);
  try {
    await vi.advanceTimersByTimeAsync(4_000);
    expect(settled).toBe(false);
  } finally {
    abort.abort();
    await observed;
    transport.close();
    vi.restoreAllMocks();
    vi.useRealTimers();
  }
});

it("admits three media packets and a close while both chat request slots are held", async () => {
  const slots = new RequestSlots();
  await Promise.all([slots.acquire("normal"), slots.acquire("normal")]);
  let media = 0;
  const packets = Array.from({ length: 3 }, () => slots.acquire("call").then(() => { media++; }));
  await Promise.resolve();
  expect(media).toBe(3);
  await slots.acquire("call"); // the close/control request keeps its own capacity
  expect(slots.inUse).toBe(6);
  let nextCall = false;
  let nextNormal = false;
  const queuedCall = slots.acquire("call").then(() => { nextCall = true; });
  const queuedNormal = slots.acquire("normal").then(() => { nextNormal = true; });
  await Promise.resolve();
  expect(nextCall || nextNormal).toBe(false);
  slots.release("normal");
  await queuedNormal;
  expect(nextCall).toBe(false);
  slots.release("call");
  await queuedCall;
  expect(slots.inUse).toBe(6);
  await Promise.all(packets);
  for (let i = 0; i < 4; i++) slots.release("call");
  for (let i = 0; i < 2; i++) slots.release("normal");
  expect(slots.inUse).toBe(0);
});

it("queues a startup burst within the computer's HTTP exchange bound", async () => {
  await sodium.ready;
  vi.stubGlobal("location", new URL("https://relay.example/relay/v1/phone/"));
  const transport = new PhoneTransport({} as ConstructorParameters<typeof PhoneTransport>[0], () => {});
  const internal = transport as unknown as {
    packet: (packet: Record<string, unknown>) => Promise<Record<string, unknown>>;
  };
  vi.spyOn(transport, "connect").mockResolvedValue();
  const outstanding = new Set<string>();
  let maximum = 0;
  vi.spyOn(internal, "packet").mockImplementation(async (packet) => {
    const id = packet.id as string;
    if (packet.op === "start") {
      if (outstanding.size >= 16) throw new Error("Too many app requests. Try again shortly.");
      outstanding.add(id);
      maximum = Math.max(maximum, outstanding.size);
    } else if (packet.op === "read") {
      outstanding.delete(id);
      return { type: "headers", status: 200, headers: [], end: true };
    } else if (packet.op === "cancel") outstanding.delete(id);
    return { ok: true };
  });
  try {
    const results = await Promise.allSettled(Array.from({ length: 24 }, (_, i) =>
      transport.fetch(`/agents/headshot?test=${i}`, { method: "PUT", body: "upload" }),
    ));
    expect(results.every((result) => result.status === "fulfilled")).toBe(true);
    expect(maximum).toBeLessThanOrEqual(16);
    expect(outstanding.size).toBe(0);
  } finally {
    transport.close();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  }
});

it("holds streaming exchanges through cancellation ACK and removes aborted queued requests", async () => {
  await sodium.ready;
  vi.stubGlobal("location", new URL("https://relay.example/relay/v1/phone/"));
  const transport = new PhoneTransport({} as ConstructorParameters<typeof PhoneTransport>[0], () => {});
  const internal = transport as unknown as {
    packet: (packet: Record<string, unknown>) => Promise<Record<string, unknown>>;
  };
  vi.spyOn(transport, "connect").mockResolvedValue();
  const starts: string[] = [];
  const reads = new Map<string, number>();
  const reading = new Map<string, (event: Record<string, unknown>) => void>();
  let acknowledge = () => {};
  const ack = new Promise<void>((resolve) => { acknowledge = resolve; });
  let holdCancel = true;
  vi.spyOn(internal, "packet").mockImplementation(async (packet) => {
    const id = packet.id as string;
    if (packet.op === "start") starts.push(packet.path as string);
    if (packet.op === "read") {
      const count = reads.get(id) ?? 0;
      reads.set(id, count + 1);
      if (count === 0) return { type: "headers", status: 200, headers: [] };
      return new Promise((resolve) => reading.set(id, resolve));
    }
    if (packet.op === "cancel") {
      if (holdCancel) await ack;
      reading.get(id)?.({ type: "end" });
    }
    return { ok: true };
  });
  try {
    const responses = await Promise.all(Array.from({ length: 12 }, (_, i) => transport.fetch(`/sessions/stream${i}`)));
    const next = transport.fetch("/sessions/next");
    const abort = new AbortController();
    const cancelled = transport.fetch("/sessions/never", { signal: abort.signal });
    const rejected = expect(cancelled).rejects.toMatchObject({ name: "AbortError" });
    await Promise.resolve();
    abort.abort();
    await rejected;
    expect(starts).toHaveLength(12);
    const cancellation = responses[0].body!.cancel();
    await Promise.resolve();
    expect(starts).toHaveLength(12);
    holdCancel = false;
    acknowledge();
    await cancellation;
    const replacement = await next;
    expect(starts).toHaveLength(13);
    expect(starts).not.toContain("/sessions/never");
    await Promise.all([...responses.slice(1), replacement].map((response) => response.body!.cancel()));
  } finally {
    acknowledge();
    transport.close();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  }
});

it("invalidates queued HTTP work and fences old stream cleanup after a rekey", async () => {
  await sodium.ready;
  vi.stubGlobal("location", new URL("https://relay.example/relay/v1/phone/"));
  const transport = new PhoneTransport({} as ConstructorParameters<typeof PhoneTransport>[0], () => {});
  const internal = transport as unknown as {
    incoming: Uint8Array | null;
    resetPacketSession: () => void;
    packet: (packet: Record<string, unknown>) => Promise<Record<string, unknown>>;
  };
  internal.incoming = new Uint8Array(32).fill(1);
  vi.spyOn(transport, "connect").mockResolvedValue();
  const starts: string[] = [];
  const reads = new Map<string, number>();
  const reading = new Map<string, (event: Record<string, unknown>) => void>();
  const packets = vi.spyOn(internal, "packet").mockImplementation(async (packet) => {
    const id = packet.id as string;
    if (packet.op === "start") starts.push(packet.path as string);
    if (packet.op === "read") {
      const count = reads.get(id) ?? 0;
      reads.set(id, count + 1);
      if (count === 0) return { type: "headers", status: 200, headers: [] };
      return new Promise((resolve) => reading.set(id, resolve));
    }
    if (packet.op === "cancel") reading.get(id)?.({ type: "end" });
    return { ok: true };
  });
  try {
    const old = await Promise.all(Array.from({ length: 12 }, () => transport.fetch("/sessions/old")));
    const queued = transport.fetch("/sessions/never");
    const rejected = expect(queued).rejects.toThrow("Connection to your computer was lost");
    await Promise.resolve();
    internal.resetPacketSession();
    await rejected;
    internal.incoming = new Uint8Array(32).fill(2);
    const current = await Promise.all(Array.from({ length: 12 }, () => transport.fetch("/sessions/current")));
    const count = packets.mock.calls.length;
    await Promise.all(old.map((response) => response.body!.cancel()));
    expect(packets.mock.calls).toHaveLength(count); // no old cancel into the new keys
    const next = transport.fetch("/sessions/next");
    await Promise.resolve();
    expect(starts).toHaveLength(24); // old releases did not free current leases
    await current[0].body!.cancel();
    const replacement = await next;
    expect(starts).toHaveLength(25);
    expect(starts).not.toContain("/sessions/never");
    await Promise.all([...current.slice(1), replacement].map((response) => response.body!.cancel()));
  } finally {
    for (const resolve of reading.values()) resolve({ type: "end" });
    transport.close();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  }
});

it("keeps approval while fencing stale and failed encrypted sessions", async () => {
  await sodium.ready;
  const host = sodium.crypto_sign_keypair();
  const hostKey = sodium.to_hex(host.publicKey);
  let hostSession = OLD_SESSION;
  const sockets: FakeSocket[] = [];
  class FakeSocket {
    static OPEN = 1;
    readyState = 1;
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: ((event: { code: number }) => void) | null = null;
    constructor() {
      sockets.push(this);
      queueMicrotask(() => this.emit([{ key: hostKey, session: hostSession }]));
    }
    frame(frame: unknown) { this.onmessage?.({ data: JSON.stringify(frame) }); }
    emit(peers: unknown[]) { this.frame({ type: "peers", peers }); }
    close() {
      if (this.readyState !== 1) return;
      this.readyState = 2; // browser close events arrive later
    }
    finishClose() {
      this.readyState = 3;
      this.onclose?.({ code: 1000 });
    }
  }
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("location", new URL("https://relay.example/relay/v1/phone/"));
  vi.stubGlobal("localStorage", { setItem: vi.fn() });
  vi.stubGlobal("sessionStorage", { removeItem: vi.fn() });
  const identity = { id: "a".repeat(32), room: "b".repeat(64), host: hostKey,
    seed: sodium.to_hex(sodium.randombytes_buf(32)), expires: 1, approved: true };
  const transport = new PhoneTransport(identity, () => {});
  const internal = transport as unknown as {
    request: (method: string) => Promise<unknown>;
    outgoing: Uint8Array | null;
    hostBox: Uint8Array;
    key: string;
    roomHash: string;
    session: string;
    pending: Map<string, { method: string; reject: (error: Error) => void; resolve: () => void }>;
  };
  const requests = vi.spyOn(internal, "request").mockImplementation(async (method) =>
    method === "phone_session"
      ? { protocol: "kollab-phonelink/1", public_key: sodium.to_hex(sodium.crypto_box_keypair().publicKey) }
      : { accepted: true },
  );
  let releaseHash = () => {};
  try {
    await transport.connect();
    const firstKeys = internal.outgoing;
    expect(firstKeys).not.toBeNull();
    sockets[0].emit([]);
    sockets[0].emit([{ key: hostKey, session: OLD_SESSION }]);
    expect(sockets[0].readyState).toBe(1);
    expect(internal.outgoing).toBe(firstKeys);
    hostSession = NEW_SESSION;
    sockets[0].emit([{ key: hostKey, session: NEW_SESSION }]);
    expect(sockets[0].readyState).toBe(2);
    expect(internal.outgoing).toBeNull();
    expect(identity.approved).toBe(true);
    await transport.connect();
    expect(sockets).toHaveLength(2);
    expect(requests.mock.calls.filter(([method]) => method === "phone_session")).toHaveLength(2);
    expect(internal.outgoing).not.toBeNull();
    expect(internal.outgoing).not.toEqual(firstKeys);
    const secondKeys = internal.outgoing;
    sockets[0].finishClose();
    expect(internal.outgoing).toBe(secondKeys);

    // A roster update can land while WebCrypto is still deriving keys. A late
    // handshake must never mark its closed socket connected or restore keys.
    const digest = crypto.subtle.digest.bind(crypto.subtle);
    const hashing = new Promise<void>((resolve) => { releaseHash = resolve; });
    let digests = 0;
    vi.spyOn(crypto.subtle, "digest").mockImplementation(async (...args) => {
      const result = await digest(...args);
      if (++digests === 2) await hashing;
      return result;
    });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    hostSession = OLD_SESSION;
    sockets[1].emit([{ key: hostKey, session: OLD_SESSION }]);
    const rekeying = transport.connect();
    void rekeying.catch(() => {});
    await vi.waitFor(() => expect(digests).toBe(2));
    expect(transport.connect()).toBe(rekeying);
    hostSession = NEW_SESSION;
    sockets[2].emit([{ key: hostKey, session: NEW_SESSION }]);
    sockets[2].finishClose();
    await vi.advanceTimersByTimeAsync(3_000); // retry fires while the closed handshake still waits
    vi.useRealTimers();
    releaseHash();
    await expect(rekeying).rejects.toThrow();
    expect(internal.outgoing).toBeNull();
    await vi.waitFor(() => {
      expect(sockets).toHaveLength(4);
      expect(internal.outgoing).not.toBeNull();
    });
    const reject = vi.fn();
    const replyTo = "d".repeat(32);
    internal.pending.set(replyTo, { method: "phone_http", reject, resolve: () => {} });
    const nonce = sodium.randombytes_buf(24);
    const id = "e".repeat(32);
    const now = Math.floor(Date.now() / 1000);
    const body = { v: 1, from: hostKey, to: internal.key, from_session: hostSession,
      to_session: internal.session, room: internal.roomHash, id, sent_at: now,
      expires_at: now + 30, kind: "response",
      payload: { reply_to: replyTo, method: "phone_http", error: "failed" } };
    const sealed = sodium.crypto_box_easy_afternm(sodium.from_string(JSON.stringify(body)), nonce, internal.hostBox);
    const bytes = new Uint8Array(nonce.length + sealed.length);
    bytes.set(nonce); bytes.set(sealed, nonce.length);
    const frame = { type: "message", from: hostKey, session: hostSession, id, ciphertext: "" };
    bytes[bytes.length - 1] ^= 1;
    sockets[3].frame({ ...frame, ciphertext: Buffer.from(bytes).toString("base64") });
    expect(sockets[3].readyState).toBe(1); // unauthenticated corruption cannot close the channel
    expect(reject).not.toHaveBeenCalled();
    bytes[bytes.length - 1] ^= 1;
    const callsBeforeFailure = requests.mock.calls.length;
    sockets[3].frame({ ...frame, ciphertext: Buffer.from(bytes).toString("base64") });
    expect(reject).toHaveBeenCalled();
    expect(sockets[3].readyState).toBe(2);
    expect(internal.outgoing).toBeNull();
    expect(identity.approved).toBe(true);
    expect(requests.mock.calls.slice(callsBeforeFailure).some(([method]) => method === "phone_http")).toBe(false);
  } finally {
    releaseHash();
    transport.close();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  }
});

it("accepts reordered replies once while rejecting replays and stale sequences", () => {
  const replies = new ReplySequenceWindow();
  expect(replies.accept(1)).toBe(true);
  expect(replies.accept(0)).toBe(true);
  expect(replies.accept(0)).toBe(false);
  expect(replies.accept(1)).toBe(false);
  expect(replies.accept(-1)).toBe(false);
  expect(replies.accept(Number.MAX_SAFE_INTEGER + 1)).toBe(false);
  expect(replies.accept(512)).toBe(true);
  expect(replies.accept(0)).toBe(false);
  expect(replies.accept(2)).toBe(true);
});

it("sends a later short packet after an earlier compressed packet", async () => {
  const encoder = new TextEncoder();
  const wire = new WireSequence();
  const sent: number[] = [];
  const send = (payload: string) => wire.run(async (seq) => {
    await encodePhoneFrame(encoder.encode(JSON.stringify({ seq, payload })), true);
    return seq;
  }).then((seq) => { sent.push(seq); });
  await Promise.all([send("x".repeat(20_000)), send("short")]);
  expect(sent).toEqual([0, 1]);
});

// The engine legitimately restarts (updates, crashes, lease pruning) and
// re-registers with a fresh relay session. A brief same-session absence gets
// a grace window. A changed host session keeps pairing but needs fresh keys.
describe("phone transport host snapshot tolerance", () => {
  it("distinguishes a changed host session from a missing host", () => {
    expect(
      hostSnapshotDecision(
        [{ key: HOST, session: NEW_SESSION }],
        HOST,
        OLD_SESSION,
        0,
        NOW,
      ),
    ).toEqual({ action: "changed", session: NEW_SESSION });
  });

  it("keeps the link while the host is missing inside the grace window", () => {
    const since = NOW - 5_000;
    expect(hostSnapshotDecision([], HOST, OLD_SESSION, since, NOW)).toEqual({
      action: "absent",
      missingSinceMs: since,
    });
  });

  it("starts the grace clock on the first absent snapshot", () => {
    expect(hostSnapshotDecision([], HOST, OLD_SESSION, 0, NOW)).toEqual({
      action: "absent",
      missingSinceMs: NOW,
    });
  });

  it("loses the link only after the grace window lapses", () => {
    expect(
      hostSnapshotDecision(
        [],
        HOST,
        OLD_SESSION,
        NOW - HOST_MISSING_GRACE_MS,
        NOW,
      ),
    ).toEqual({ action: "lost", why: "missing" });
  });

  it("still requires the host before the first settle", () => {
    expect(hostSnapshotDecision([], HOST, "", 0, NOW)).toEqual({
      action: "lost",
      why: "missing",
    });
  });

  it("rejects invalid rosters and invalid host sessions", () => {
    // biome-ignore lint/suspicious/noExplicitAny: exercising non-array input.
    const notAnArray: any = { peers: [] };
    expect(hostSnapshotDecision(notAnArray, HOST, OLD_SESSION, 0, NOW)).toEqual({
      action: "lost",
      why: "invalid",
    });
    expect(
      hostSnapshotDecision(
        new Array(257).fill({ key: HOST, session: OLD_SESSION }),
        HOST,
        OLD_SESSION,
        0,
        NOW,
      ),
    ).toEqual({ action: "lost", why: "invalid" });
    expect(
      hostSnapshotDecision(
        [{ key: HOST, session: "not-a-session" }],
        HOST,
        OLD_SESSION,
        0,
        NOW,
      ),
    ).toEqual({ action: "lost", why: "invalid" });
  });

  it("reports a present, unchanged host session", () => {
    expect(
      hostSnapshotDecision(
        [{ key: HOST, session: OLD_SESSION }],
        HOST,
        OLD_SESSION,
        NOW,
        NOW,
      ),
    ).toEqual({ action: "present", session: OLD_SESSION });
  });
});
