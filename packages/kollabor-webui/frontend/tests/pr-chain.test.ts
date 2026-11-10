import { describe, expect, it } from "vitest";
import type { PrChainItem } from "../src/api";
import { describeEvent, itemUrl, latestOnly, shortTime, sortItems, statusLabel, statusTone } from "../src/components/pr-chain/prChain";

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

  it("shortens stamps and describes events", () => {
    expect(shortTime("2026-11-09T17:27:03")).toBe("17:27");
    expect(shortTime(undefined)).toBe("");
    expect(describeEvent({ pr: 960, stage: "review", verdict: "PASS" })).toBe("PR 960 Review: Pass");
    expect(describeEvent({ issue: 893, stage: "issue-end", status: "BUILD-FAILED" })).toBe("Issue 893 Issue ended: Build failed");
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

it("only the newest request counts, so a slow older poll cannot overwrite newer data", () => {
  const next = latestOnly();
  const first = next();
  const second = next();
  expect(first()).toBe(false);
  expect(second()).toBe(true);
  const third = next();
  expect(second()).toBe(false);
  expect(third()).toBe(true);
});
