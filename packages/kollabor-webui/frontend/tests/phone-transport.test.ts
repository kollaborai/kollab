import { describe, expect, it } from "vitest";
import {
  hostSnapshotDecision,
  HOST_MISSING_GRACE_MS,
  RequestSlots,
  WireSequence,
} from "../src/phonelink/phone-transport";

const HOST = "a".repeat(64);
const OLD_SESSION = "1".repeat(32);
const NEW_SESSION = "2".repeat(32);
const NOW = 1_000_000;

// The engine legitimately restarts (updates, crashes, lease pruning) and
// re-registers with a fresh relay session. The tolerance rules below keep a
// connected phone from tearing its relay socket down on every such blip —
// the 2026-10-30 reconnect loop did exactly that.
describe("phone transport host snapshot tolerance", () => {
  it("adopts a host session change instead of losing the link", () => {
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

// Let queued microtasks and timers run, so a waiter either wakes or stays put.
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function track(promise: Promise<void>) {
  let done = false;
  void promise.then(() => {
    done = true;
  });
  return () => done;
}

describe("request slot lanes", () => {
  it("never lets normal traffic take more than two slots", async () => {
    const slots = new RequestSlots();
    await slots.acquire("normal");
    await slots.acquire("normal");
    expect(slots.inUse).toBe(2);
    const third = track(slots.acquire("normal"));
    await settle();
    expect(third()).toBe(false);
    expect(slots.inUse).toBe(2);
  });

  it("gives a call op the third slot while two long polls hold the others", async () => {
    const slots = new RequestSlots();
    await slots.acquire("normal"); // event long poll
    await slots.acquire("normal"); // event long poll
    const call = track(slots.acquire("call"));
    await settle();
    expect(call()).toBe(true);
    expect(slots.inUse).toBe(3);
  });

  it("does not let a queued normal request take the slot a call frees", async () => {
    const slots = new RequestSlots();
    await slots.acquire("normal");
    await slots.acquire("normal");
    await slots.acquire("call");
    const queuedNormal = track(slots.acquire("normal"));
    slots.release(); // a long poll ends; a call still needs its slot
    await settle();
    expect(queuedNormal()).toBe(false);
    expect(slots.inUse).toBe(2);
  });

  it("starts a call without waiting behind a chat backlog", async () => {
    const slots = new RequestSlots();
    await slots.acquire("normal");
    await slots.acquire("normal");
    const backlog = [1, 2, 3, 4, 5].map(() => track(slots.acquire("normal")));
    const tick = track(slots.acquire("call"));
    await settle();
    expect(tick()).toBe(true);
    expect(backlog.map((done) => done())).toEqual([false, false, false, false, false]);
  });

  it("wakes queued normal requests in arrival order", async () => {
    const slots = new RequestSlots();
    await slots.acquire("normal");
    await slots.acquire("normal");
    const order: string[] = [];
    void slots.acquire("normal").then(() => order.push("first"));
    void slots.acquire("normal").then(() => order.push("second"));
    slots.release();
    await settle();
    expect(order).toEqual(["first"]);
    slots.release();
    await settle();
    expect(order).toEqual(["first", "second"]);
  });
});

describe("wire sequence", () => {
  it("assigns seq in call order even when encodes finish out of order", async () => {
    const wire = new WireSequence();
    const delays = [30, 0, 10, 0];
    const wireOrder: number[] = [];
    const jobs = delays.map((delay) =>
      wire.run(async (seq) => {
        await new Promise((resolve) => setTimeout(resolve, delay));
        wireOrder.push(seq);
        return seq;
      }),
    );
    const seqs = await Promise.all(jobs);
    expect(seqs).toEqual([0, 1, 2, 3]);
    expect(wireOrder).toEqual([0, 1, 2, 3]);
  });

  it("keeps going after a failed encode without reusing its seq", async () => {
    const wire = new WireSequence();
    const failed = wire.run(async () => {
      throw new Error("encode failed");
    });
    await expect(failed).rejects.toThrow("encode failed");
    const next = await wire.run(async (seq) => seq);
    expect(next).toBe(1);
  });
});
