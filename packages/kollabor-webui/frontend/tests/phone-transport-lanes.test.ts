import { describe, expect, it } from "vitest";
import { RequestSlots } from "../src/phonelink/phone-transport";

// Let queued microtasks and timers run, so a waiter either wakes or stays put.
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function track(promise: Promise<void>) {
  let done = false;
  void promise.then(() => {
    done = true;
  });
  return () => done;
}

describe("RequestSlots lanes", () => {
  it("caps normal requests at two of the three slots", async () => {
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
    // A long poll ends; the queued normal must keep waiting, a call still needs it.
    slots.release();
    await settle();
    expect(queuedNormal()).toBe(false);
    expect(slots.inUse).toBe(2);
  });

  it("starves no audio: a call waits only for a call-lane slot, not chat backlog", async () => {
    const slots = new RequestSlots();
    await slots.acquire("normal");
    await slots.acquire("normal");
    const backlog = [1, 2, 3, 4, 5].map(() => track(slots.acquire("normal")));
    const tick = track(slots.acquire("call"));
    await settle();
    expect(tick()).toBe(true);
    expect(backlog.map((done) => done())).toEqual([false, false, false, false, false]);
  });

  it("wakes a queued normal once a normal slot frees, in arrival order", async () => {
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
