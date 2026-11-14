import { describe, expect, it } from "vitest";
import type { PrChainItem } from "../src/api";
import { describeEvent, listNote, inOrder, itemDetail, itemUrl, shortTime, sortItems, statusLabel, statusTone, inFlight } from "../src/components/pr-chain/prChain";

const item = (over: Partial<PrChainItem>): PrChainItem => ({ kind: "pr", number: 1, claimed: false, ...over });

describe("pr-chain helpers", () => {
  it("links a PR to /pull and an issue to /issues", () => {
    expect(itemUrl(item({ number: 960 }))).toBe("https://github.com/kollaborai/kollab/pull/960");
    expect(itemUrl(item({ kind: "issue", number: 893 }))).toBe("https://github.com/kollaborai/kollab/issues/893");
  });

  it("puts items that need a person first, finished items last, newest first inside a group", () => {
    const rows = sortItems([
      item({ number: 1, status: "MERGED", updated: "2026-11-09T18:00:00" }),
      item({ number: 2, status: "READY", updated: "2026-11-09T10:00:00" }),
      item({ number: 3, status: "AWAIT-HUMAN", updated: "2026-11-09T09:00:00" }),
      item({ number: 4, status: "REVIEWING", claimed: true, updated: "2026-11-09T08:00:00" }),
      item({ number: 5, status: "READY", updated: "2026-11-09T11:00:00" }),
    ]);
    expect(rows.map((r) => r.number)).toEqual([3, 4, 5, 2, 1]);
  });

  it("reads the tone from status and claim", () => {
    expect(statusTone(item({ status: "UI-FAILED" }))).toBe("failed");
    expect(statusTone(item({ status: "BUILD-FAILED" }))).toBe("failed");
    expect(statusTone(item({ status: "ERROR" }))).toBe("failed");
    expect(statusTone(item({ status: "AWAIT-HUMAN" }))).toBe("attention");
    expect(statusTone(item({ status: "MERGED" }))).toBe("done");
    expect(statusTone(item({ status: "REVIEWING", claimed: true }))).toBe("active");
    expect(statusTone(item({ status: "READY" }))).toBe("idle");
  });

  it("classifies every stop the runner can end on", () => {
    for (const status of ["GATE-FAILED", "NO-VERDICT", "UI-NO-VERDICT", "CI-RED"]) {
      expect(statusTone(item({ status, claimed: false }))).toBe("failed");
    }
    for (const status of ["NEEDS-HUMAN", "LAND-STOPPED"]) {
      expect(statusTone(item({ status, claimed: false }))).toBe("attention");
    }
    expect(statusLabel("CI-RED")).toBe("CI red");
  });

  it("shortens stamps and describes events", () => {
    expect(shortTime("2026-11-09T17:27:03", new Date(2026, 10, 9, 18, 0))).toBe("17:27");
    expect(shortTime(undefined)).toBe("");
    expect(describeEvent({ pr: 960, stage: "review", verdict: "PASS" })).toBe("PR 960 Review: Pass");
    expect(describeEvent({ issue: 893, stage: "issue-end", status: "BUILD-FAILED" })).toBe("Issue 893 Ended: Build failed");
    expect(describeEvent({ pr: 7, stage: "human-gate", status: "AWAIT-HUMAN" })).toBe("PR 7 Waiting for you");
  });

  it("names workflow tokens in plain words", () => {
    expect(statusLabel("AWAIT-HUMAN")).toBe("Waiting for you");
    expect(statusLabel("BUILD-FAILED")).toBe("Build failed");
    expect(statusLabel("UI-FAILED")).toBe("UI failed");
    expect(statusLabel("ERROR")).toBe("Error");
    expect(statusLabel("REVIEWING")).toBe("Reviewing");
    expect(statusLabel("MERGED")).toBe("Merged");
    expect(statusLabel("ui-review")).toBe("UI review");
    expect(statusLabel("SOME-NEW-STATE")).toBe("Some new state");
    expect(statusLabel(undefined)).toBe("Unknown");
  });
});

describe("inOrder", () => {
  it("drops an answer older than one already shown, so a slow poll cannot overwrite newer data", () => {
    const next = inOrder();
    const first = next();
    const second = next();
    expect(second()).toBe(true);
    expect(first()).toBe(false);
  });

  it("shows every answer that arrives in order, even when each request started before the previous one finished", () => {
    const next = inOrder();
    const first = next();
    const second = next();
    const third = next();
    expect(first()).toBe(true);
    expect(second()).toBe(true);
    expect(third()).toBe(true);
  });

  it("stays true when asked again by the request that was already shown", () => {
    const next = inOrder();
    const only = next();
    expect(only()).toBe(true);
    expect(only()).toBe(true);
  });
});

describe("statusLabel with names that exist on every object", () => {
  it("reads them as plain words, never as inherited properties", () => {
    expect(statusLabel("__proto__")).toBe("Proto");
    expect(statusLabel("constructor")).toBe("Constructor");
    expect(statusLabel("toString")).toBe("Tostring");
    expect(statusLabel("hasOwnProperty")).toBe("Hasownproperty");
  });
});

describe("inFlight", () => {
  it("is busy from begin until the matching end, and an end counts once", () => {
    const requests = inFlight();
    expect(requests.busy()).toBe(false);
    const first = requests.begin().end;
    const second = requests.begin().end;
    expect(requests.busy()).toBe(true);
    first();
    first();
    expect(requests.busy()).toBe(true);
    second();
    expect(requests.busy()).toBe(false);
  });
});

describe("inFlight with a request that never settles", () => {
  it("stops counting a request after the limit so refreshing can resume", () => {
    let clock = 0;
    const requests = inFlight(30_000, () => clock);
    requests.begin();
    clock = 29_999;
    expect(requests.busy()).toBe(true);
    clock = 30_001;
    expect(requests.busy()).toBe(false);
  });
});

describe("inFlight forgets what it gave up on", () => {
  it("drops a request that outlived the limit when the next one begins, so none pile up", () => {
    let clock = 0;
    const requests = inFlight(30_000, () => clock);
    for (let i = 0; i < 5; i += 1) {
      requests.begin();
      clock += 31_000;
    }
    expect(requests.pending()).toBe(1);
  });
});

describe("statusLabel keeps acronyms", () => {
  it("writes PR, CI, UI and ID in capitals inside an unknown token", () => {
    expect(statusLabel("PR-OPEN")).toBe("PR open");
    expect(statusLabel("ci_running")).toBe("CI running");
    expect(statusLabel("ui-id-check")).toBe("UI ID check");
    expect(statusLabel("pricing")).toBe("Pricing");
  });
});

describe("shortTime with a date other than today", () => {
  const now = new Date(2026, 10, 9, 18, 0, 0);
  it("shows only the time for today", () => {
    expect(shortTime("2026-11-09T17:27:03", now)).toBe("17:27");
  });
  it("adds the month and day for another day", () => {
    expect(shortTime("2026-11-08T17:27:03", now)).toBe("11-08 17:27");
  });
  it("shows anything else as given", () => {
    expect(shortTime("soon", now)).toBe("soon");
    expect(shortTime(undefined, now)).toBe("");
  });
});

describe("inFlight cancels what it gives up on", () => {
  it("aborts a request that outlived the limit when the next one begins", () => {
    let clock = 0;
    const requests = inFlight(30_000, () => clock);
    const stale = requests.begin();
    clock = 31_000;
    const fresh = requests.begin();
    expect(stale.signal.aborted).toBe(true);
    expect(fresh.signal.aborted).toBe(false);
  });

  it("does not abort a request that settled", () => {
    const requests = inFlight();
    const done = requests.begin();
    done.end();
    expect(done.signal.aborted).toBe(false);
  });

  it("aborts every open request on abortAll", () => {
    const requests = inFlight();
    const a = requests.begin();
    const b = requests.begin();
    requests.abortAll();
    expect([a.signal.aborted, b.signal.aborted]).toEqual([true, true]);
    expect(requests.pending()).toBe(0);
  });
});

describe("itemDetail", () => {
  const old = { why: "conflict with main", last_verdict: "FAIL" };

  it("shows the reason and verdict of a stopped run", () => {
    expect(itemDetail(item({ status: "NEEDS-HUMAN", ...old }))).toBe("Fail · conflict with main");
  });

  it("hides a past failure while the run is going again", () => {
    expect(itemDetail(item({ status: "running", ...old }))).toBe("");
    expect(itemDetail(item({ status: "REVIEWING", claimed: true, ...old }))).toBe("");
  });

  it("keeps the verdict but not an old reason once a run is ready", () => {
    expect(itemDetail(item({ status: "READY", last_verdict: "PASS", why: "conflict with main" }))).toBe("Pass");
  });
});

describe("the note under the list", () => {
  it("is shown whenever the list was cut, even if nothing readable was found", () => {
    expect(listNote({ root_found: true, items: [], events: [], truncated: true })).toBe("Some items are not listed.");
    expect(listNote({ root_found: true, items: [item({})], events: [], truncated: true })).toBe("Some items are not listed.");
  });

  it("is absent when the list is whole", () => {
    expect(listNote({ root_found: true, items: [item({})], events: [] })).toBeNull();
    expect(listNote({ root_found: true, items: [], events: [], truncated: false })).toBeNull();
  });
});
